# FocalPoint — Data Sources

## Overview

FocalPoint uses four data sources. Each has a distinct role.
No source duplicates another's function.

| Source       | Role | Update Frequency | Auth |
|--------------|------|-----------------|------|
| GDELT Cloud  | Real-time conflict events | Continuous | API key |
| GDELT Doc API | News sentiment + media signals | Every 15 min | None |
| CPJ          | Historical journalist incidents | Daily | None (local CSV) |
| RSF          | Country press freedom baseline | Annual | None |

ACLED is preserved as `backend/ingestion/acled_connector_disabled.py` and
can be reactivated if API access is granted. See the ACLED section below.

All credentials stored in .env — never hardcoded.

---

## GDELT Cloud (Conflict Events — active source)

**Base URL:** `https://gdeltcloud.com/api/v2/events`

**Auth:** Bearer token — sent as `Authorization: Bearer {GDELT_CLOUD_API_KEY}` header.
Key stored in `.env` as `GDELT_CLOUD_API_KEY`.

**Role:** Real-time conflict event data. Provides geolocated events with
actor and event-type fields that feed directly into severity scoring.

**Query parameters sent by FocalPoint:**
- `country` — country name (e.g. `Palestine`)
- `event_family=conflict`
- `date_start` — start of query window, formatted `YYYY-MM-DD` (today − (days − 1))
- `date_end` — end of query window, formatted `YYYY-MM-DD` (today)
- `has_fatalities=true` — filter to events with confirmed casualties (omitted for some countries)
- `sort=recent`
- `limit` — 20 for backend, 10 for on-device

The date window is required — without `date_start` / `date_end` the API returns 0
results regardless of country. The `days` parameter in `fetch_events()` controls
the window width; `days=1` queries today only, `days=7` queries the last 7 days.

Note: `has_fatalities=true` can return 0 results for some countries (Iran, Sudan,
Myanmar, Yemen, Syria). These countries are listed in `NO_FATALITIES_FILTER_COUNTRIES`
in `config.py` — the filter is omitted for them and the `fatalities` field on each
event may be `null` rather than absent.

**Real API response structure (confirmed from live curl):**
```json
{
  "success": true,
  "data": [
    {
      "id": "conflict_...",
      "event_date": "2026-04-23",
      "category": "Armed Clash",
      "subcategory": "Armed clash",
      "fatalities": 2,
      "summary": "...",
      "geo": {
        "country": "...",
        "admin1": "...",
        "location": "...",
        "latitude": 32.009,
        "longitude": 35.311
      },
      "actors": [
        {"name": "...", "country": "...", "role": "actor1"},
        {"name": "...", "country": "...", "role": "actor2"}
      ],
      "metrics": {
        "significance": 0.374,
        "goldstein_scale": -9,
        "confidence": 0.83,
        "article_count": 1
      }
    }
  ]
}
```

**Field mapping to GdeltCloudEvent:**
- `data[]` — top-level list key (not `events`)
- `category` → `event_type`
- `subcategory` → `sub_event_type`
- `fatalities` — top-level int, `None` ≠ zero
- `geo.latitude/longitude/country/admin1/location` — nested object
- `actors[].role` — `"actor1"` or `"actor2"` used to identify parties
- `metrics.confidence` — confidence score (not top-level)
- `metrics.goldstein_scale` — conflict intensity -10 to +10

**Example query (recent conflict events for Palestine, last 7 days):**
```
GET https://gdeltcloud.com/api/v2/events
    ?country=Palestine&event_family=conflict
    &date_start=2026-04-19&date_end=2026-04-25
    &has_fatalities=true&sort=recent&limit=20
Authorization: Bearer {GDELT_CLOUD_API_KEY}
```

**Free tier:** 100 queries/month. Cache aggressively — see caching.md.

**Pydantic model:** `backend/ingestion/gdeltcloud_connector.py` → `GdeltCloudEvent`
(with nested `GdeltCloudGeo`, `GdeltCloudActor`, `GdeltCloudMetrics`)

**Redis key pattern:** `gdeltcloud:{country}:{days}:{has_fatalities}`
**TTL:** 28800 seconds (8 hours — preserves free-tier quota)

---

## ACLED (Armed Conflict Location & Event Data) — DISABLED

> **Status: disabled.** The ACLED OAuth2 API requires institutional access
> that has not been granted. The connector is preserved in full at
> `backend/ingestion/acled_connector_disabled.py` and its tests at
> `backend/tests/test_acled_connector_disabled.py`.
>
> To reactivate: rename the file to `acled_connector.py`, restore the
> ACLED_* env vars in `.env` (template kept commented in `.env.example`),
> and re-enable the ACLED import in `backend/alerts/severity_scorer.py`.

**Would have used:** https://api.acleddata.com/acled/read (OAuth2 Bearer token)

**Credentials needed (kept commented in .env.example):**
- ACLED_USERNAME
- ACLED_PASSWORD
- ACLED_TOKEN_URL = https://acleddata.com/oauth/token

---

## GDELT 2.0 Doc API (News Sentiment — active source)

**Base URL:** https://api.gdeltproject.org/api/v2/doc/doc

**Auth:** None required

**Key fields used:**
- url — source article URL (used as source citation)
- title — article headline
- seendate — publication datetime
- sourcecountry — country of origin
- language — article language
- tone — sentiment score (negative = more hostile/dangerous coverage)
- domain — publisher domain

**Two-call fetch:** Every fetch makes two API calls — `mode=artlist` returns
the article list; a separate `mode=timelinetone` call returns a 15-minute
resolution time series from which `aggregate_tone` (mean of non-zero windows)
is computed. Both results are packed into a single `GdeltResponse` and cached
together under one Redis key, so callers always receive articles and tone in
one object.

**Doc API query parameters:**
- query — keyword or phrase search
- mode — artlist (article list) or timelinetone (sentiment over time)
- maxrecords — max 250
- timespan — e.g. 24H, 7D
- country — FIPS 2-letter country code

**Example query (recent conflict coverage for a region):**
```
?query=conflict+journalist+Gaza&mode=artlist&maxrecords=10
&timespan=24H&format=json
```

**Pagination:**
GDELT Doc API does not support cursor pagination natively.
Use timespan and maxrecords to bound result size.
FocalPoint uses maxrecords=10 for on-device, maxrecords=20 for backend.

**Tone interpretation:**
- Score < -5: Hostile/dangerous media environment
- Score -5 to 0: Negative but normal conflict coverage
- Score > 0: Unusually positive (rare in conflict zones)

**Pydantic model:** backend/ingestion/gdelt_connector.py → GdeltArticle

**Redis key pattern:** gdelt:{query_hash}:{timespan}
**TTL:** 900 seconds (15 minutes — matches GDELT update frequency)

---

## CPJ (Committee to Protect Journalists)

**Source:** https://cpj.org/data-api/ → "Download this database"
**Format:** CSV download — no API, no key, no account required
**Status:** CPJ REST API has been unavailable since April 2024.
Use the CSV download instead.

**How to obtain:**
1. Go to https://cpj.org/data-api/ in your browser
2. Click "Download this database"
3. Save file as backend/data/cpj_incidents.csv
4. Commit to git — this is a versioned static asset

**Primary use:**
Historical journalist incident rate per country.
Used as one input to severity scoring — countries with higher
historical incident rates get elevated baseline severity.
Loaded into memory at backend startup, indexed by country.

**Key fields used:**
- date — date of incident
- country — country where incident occurred
- type — killed, imprisoned, missing, attacked
- gender — journalist gender
- medium — print, online, television, radio
- employment — staff or freelance
- local_or_foreign — local or foreign correspondent

Note: Inspect the actual CSV column names after downloading —
field names may differ from the API documentation.
Update the Pydantic model to match actual column names exactly.

**Loading approach:**
```python
import pandas as pd

df = pd.read_csv("backend/data/cpj_incidents.csv")
# Index by country for O(1) lookup
cpj_by_country = df.groupby("country")
```

**Pydantic model:** backend/ingestion/cpj_connector.py → CpjIncident
**No Redis caching needed** — in-memory from startup
**No API key needed** — local CSV file only

---

## MapLibre React Native (Maps)

**Package:** @maplibre/maplibre-react-native
**Tile URL:** https://demotiles.maplibre.org/style.json
**Auth:** None required — completely free, no account, no key
**Usage:** Pass the tile URL directly as the mapStyle prop

```jsx
<Map mapStyle="https://demotiles.maplibre.org/style.json" />
```

For a nicer dark map style suited to a conflict intelligence tool,
Stadia Maps offers a free tier with no credit card required:
https://stadiamaps.com — sign up with email only, get an API key,
and use their Alidade Smooth Dark style instead.

No entry needed in .env for demo tile usage.

---

## RSF (Reporters Without Borders) Press Freedom Index

**Source:** https://rsf.org/en/index
**Format:** Hardcoded Python dictionary — no file, no API, no download
**Updates:** Annually — check rsf.org each May and update the dict

**Approach:**
RSF covers 180 countries with a single score per country.
It is too small and too static to warrant file loading or caching.
Store as a Python dict in backend/data/rsf_scores.py and import
directly wherever needed.

```python
# backend/data/rsf_scores.py
RSF_SCORES: dict[str, float] = {
    "Norway": 91.89,
    "Denmark": 90.27,
    "Palestine": 26.44,
    "Yemen": 18.21,
    # ... 180 countries total
    # Source: RSF World Press Freedom Index 2025
    # https://rsf.org/en/index
    # Update annually each May when new index is published
}
```

**How to populate:**
Go to https://rsf.org/en/index, copy the country scores
for all 180 countries and paste into rsf_scores.py.
Takes about 10 minutes manually.

**Score interpretation for severity scoring:**
- 75–100: Good press freedom — baseline GREEN
- 50–74: Satisfactory — baseline AMBER modifier
- 25–49: Difficult — baseline RED modifier
- 0–24: Very serious — baseline CRITICAL modifier

**Primary use:**
Country-level press freedom baseline fed into severity scoring.
Imported directly — no loading, no caching, no Pydantic model.

**No file to download, no API key, no Redis caching needed.**

---

## Environment Variables Required

```
GDELT_CLOUD_API_KEY=          # GDELT Cloud conflict events API key
GOOGLE_AI_STUDIO_API_KEY=     # Gemini API key — covers Gemma 4 models
REDIS_URL=redis://localhost:6379

# ACLED credentials — kept for reactivation if API access is granted
# ACLED_USERNAME=
# ACLED_PASSWORD=
# ACLED_TOKEN_URL=https://acleddata.com/oauth/token

Note: GOOGLE_AI_STUDIO_API_KEY is a Gemini API key from
https://aistudio.google.com — it covers both Gemini and Gemma 4 models.
No credit card required. Use model ID gemma-4-26b-a4b-it or
gemma-4-31b-it in API calls to target Gemma 4 specifically.
```

Never commit .env to git.
Add .env to .gitignore before first commit.
