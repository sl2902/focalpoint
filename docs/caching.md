# FocalPoint — Caching Strategy

## Overview

FocalPoint has three distinct caching layers serving different purposes.
Each layer is independent. A failure in one does not cascade to others.

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Backend API cache | Redis | Rate limit protection, response reuse |
| Gemma 4 response cache | Redis | Avoid redundant model calls |
| Mobile offline cache | Expo SQLite | Field use without connectivity |

---

## Layer 1 — Backend API Response Cache (Redis)

Sits between the ingestion connectors and the data source APIs.
Prevents redundant API calls. Protects against rate limits.
All TTLs are aligned to the natural update frequency of each source.

| Source           | Redis Key Pattern | TTL | Rationale |
|------------------|-----------------|-----|-----------|
| GDELT Cloud      | gdelt_cloud:{query_hash}:{timespan} | 28800s | Free tier is 100 queries/month — 8h TTL keeps usage within quota |
| GDELT Doc API    | gdelt:{query_hash}:{timespan} | 900s | Matches GDELT 15min update cadence; artlist + timelinetone cached together |

Note: CPJ data is loaded from a local static CSV at startup and
held in memory. No Redis caching needed for CPJ.
Note: RSF data is a hardcoded Python dict in backend/data/rsf_scores.py.
No loading, no caching, no Redis entry needed.

**Cache miss behaviour:**
On cache miss, connector fetches fresh from API, validates with Pydantic,
writes to Redis with TTL, returns data. Never fails silently.

**Cache hit behaviour:**
Return cached data immediately. No API call made.

**Implementation:**
Use redis-py async client. All cache reads/writes are async.
Cache key collisions prevented by including all query parameters in key hash.

---

## Layer 2 — Gemma 4 Response Cache (Redis)

Sits between the processor layer and the Gemma 4 model.
Prevents redundant model calls for identical or near-identical queries
within the same time window.

| Cache Key | TTL | Notes |
|-----------|-----|-------|
| query:{region}:{sha256_prefix(journalist_query)} | 3600s | journalist query text is Gemma context, not GDELT search term |

**When NOT to cache:**
- Responses backed by web search (`use_web_search=True`) — live results are time-sensitive
- All `/transcribe` responses — audio is ephemeral and results are request-specific
- Responses with `severity == INSUFFICIENT_DATA` — may be a transient API timeout or
  failure; caching them would serve an error response for the full TTL window

**Audio and caching:**
Audio submissions to `/query` do not bypass the cache. Audio processing ends at
`/transcribe`; `/query` only receives text. Cache key is based on region + journalist
query text regardless of whether the original input was voice or typed.

**Implementation:**
Check cache before building prompt. On hit, return cached response.
On miss, call model, validate output with Pydantic, write to Redis.

---

## Layer 3 — Mobile Offline Cache (Expo SQLite)

Persists alert data locally on the journalist's device.
Primary purpose: full functionality without internet connectivity.
Secondary purpose: instant load on app open before fresh data arrives.

**Schema:**

```sql
CREATE TABLE alerts (
  id TEXT PRIMARY KEY,
  region TEXT NOT NULL,
  severity TEXT NOT NULL,  -- GREEN, AMBER, RED, CRITICAL
  summary TEXT NOT NULL,
  source_ids TEXT NOT NULL, -- JSON array of ACLED/GDELT/CPJ citations
  timestamp INTEGER NOT NULL,
  watch_zone INTEGER DEFAULT 0  -- 1 if from journalist's watch zone
);

CREATE TABLE map_markers (
  id TEXT PRIMARY KEY,
  latitude REAL NOT NULL,
  longitude REAL NOT NULL,
  severity TEXT NOT NULL,
  alert_id TEXT REFERENCES alerts(id),
  timestamp INTEGER NOT NULL
);

CREATE TABLE cache_meta (
  key TEXT PRIMARY KEY,
  last_updated INTEGER NOT NULL
);
```

**Retention policy:**
- Keep last 100 alerts per watch zone region
- Keep last 50 alerts for non-watch-zone regions
- Purge alerts older than 7 days automatically on app open
- Map markers pruned to match alert retention

**Staleness indicator:**
The UI always shows "Last updated: {timestamp}" prominently.
When offline, all data is labelled CACHED in amber text.
The journalist always knows how fresh their data is.

**Sync behaviour:**
On connectivity restored:
1. Fetch fresh alerts from backend
2. Merge with local cache — newer timestamps win
3. Purge expired entries
4. Update cache_meta timestamps
5. Remove CACHED label from UI

---

## Cache Invalidation

FocalPoint uses TTL-based expiration exclusively.
No manual cache invalidation is implemented.
This is intentional — simpler, more reliable, and appropriate
for the update frequencies of the data sources used.

Exception: on backend restart, Redis cache is NOT flushed.
Cached data remains valid until TTL expires naturally.

---

## Failure Modes

**Redis unavailable:**
Backend falls back to direct API calls with no caching.
Latency increases but functionality is preserved.
Log warning — do not crash.

**SQLite unavailable on device:**
Mobile falls back to in-memory storage for the session.
Data does not persist between app restarts.
Show persistent warning banner in UI.

**Stale cache served after TTL miss:**
Not possible — Redis TTL expiry is hard. Cache miss triggers fresh fetch.
