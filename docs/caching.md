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
| GDELT Doc API    | gdelt:articles:{query}:{timespan} | 900s (scheduler) / 86400s (/query) | Scheduler needs fresh articles each run; /query callers tolerate stale data. Empty results (0 articles) are never cached — a transient 429 or dry spell must not poison the cache for subsequent callers |

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
GDELT Doc cache TTL is caller-controlled via the `cache_ttl` parameter on
`fetch_articles` / `fetch_articles_for_region` (default 900s). The `/query`
route passes `cache_ttl=86400`; the scheduler uses the default.

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
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  region     TEXT    NOT NULL,
  days       INTEGER NOT NULL DEFAULT 7,  -- time window (1/3/7/14/30)
  data       TEXT    NOT NULL,            -- full AlertResponse JSON blob
  fetched_at INTEGER NOT NULL             -- Unix ms timestamp of fetch
);
CREATE INDEX idx_alerts_region      ON alerts(region);
CREATE INDEX idx_alerts_region_days ON alerts(region, days);
```

`data` stores the complete `AlertResponse` JSON. `days` partitions rows by
time window so the 1d feed and 7d feed are independent cache namespaces.

**Retention policy:**
- Keep last 100 rows per `(region, days)` — trimmed after every `upsertAlert`
- On cold start, evict all rows with `fetched_at < now() - 24h` via
  `deleteAlertsOlderThan` so stale data cannot accumulate across app restarts

**Staleness check:**
None on cold start — `useAlerts` always fetches from the backend unconditionally
(stale-while-revalidate). SQLite is read and displayed immediately in parallel so
the feed is never blank while the request is in flight. `getNewestFetchedAt(days)`
is used for the cache timestamp label only, not as a staleness gate.
Pull-to-refresh always hits the backend.

**Sync behaviour:**
On cold start (connectivity available):
1. Evict rows older than 24 h
2. Read SQLite immediately and display whatever is cached (may be empty)
3. Fetch backend in parallel — always, no staleness gate
4. Write fresh data to SQLite; update display when backend responds
5. Pull-to-refresh always calls `fetchFeed()` regardless of age

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
