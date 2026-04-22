# FocalPoint — Data Sources

## Overview

FocalPoint uses four data sources. Each has a distinct role.
No source duplicates another's function.

| Source | Role | Update Frequency | Auth |
|--------|------|-----------------|------|
| ACLED  | Real-time conflict events | Continuous | API key |
| GDELT  | News sentiment + media signals | Every 15 min | None |
| CPJ    | Historical journalist incidents | Daily | API key |
| RSF    | Country press freedom baseline | Annual | None |

All credentials stored in .env — never hardcoded.

---

## ACLED (Armed Conflict Location & Event Data)

**Base URL:** https://api.acleddata.com/acled/read

**Auth:** OAuth2 Bearer token — not a static API key.
You POST your credentials to the token URL to receive a short-lived
access token, then pass it as a Bearer header on every API request.

Token URL: settings.ACLED_TOKEN_URL  # see .env

```python
# Step 1 — get token
response = requests.post(
    "https://acleddata.com/oauth/token",
    headers={"Content-Type": "application/x-www-form-urlencoded"},
    data={
        "username": settings.ACLED_USERNAME,
        "password": settings.ACLED_PASSWORD,
        "grant_type": "password",
        "client_id": "acled",
    }
)
token = response.json()["access_token"]

# Step 2 — use token on every request
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
}
```

Tokens expire — implement token refresh logic in the connector.
Cache the token in Redis with TTL slightly shorter than expiry.

**Key fields used:**
- event_id_cnty — unique event identifier (used as source citation)
- event_date — date of event
- event_type — Battles, Explosions/Remote violence, Violence against civilians, etc.
- actor1, actor2 — parties involved
- country, location — geography
- latitude, longitude — for map markers
- fatalities — integer count
- notes — human-readable event description

**Key endpoints:**
- ACLED event data: /acled/read
- CAST forecasts: /cast/read (regional conflict forecasts)

**Pagination:**
Use &page=1, &page=2 etc. Default limit 5000 rows.
FocalPoint uses limit=20 for alert generation context.

**Example query (recent events in a country):**
```
GET https://acleddata.com/api/acled/read?_format=json
    &country=Palestine&limit=20&page=1
    &fields=event_id_cnty|event_date|event_type|fatalities
    |latitude|longitude|notes
    &event_date=2026-04-01|2026-04-23&event_date_where=BETWEEN

Headers: Authorization: Bearer {token}
```

**Pydantic model:** backend/ingestion/acled_connector.py → AcledEvent

**Redis key pattern:** acled:{country}:{page}
**TTL:** 3600 seconds (1 hour)

---

## GDELT 2.0 (Global Database of Events, Language, and Tone)

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
ACLED_USERNAME=
ACLED_PASSWORD=
ACLED_TOKEN_URL=https://acleddata.com/oauth/token
GOOGLE_AI_STUDIO_API_KEY=
REDIS_URL=redis://localhost:6379

Note: GOOGLE_AI_STUDIO_API_KEY is a Gemini API key from
https://aistudio.google.com — it covers both Gemini and Gemma 4 models.
No credit card required. Use model ID gemma-4-26b-a4b-it or
gemma-4-31b-it in API calls to target Gemma 4 specifically.
```

Never commit .env to git.
Add .env to .gitignore before first commit.
