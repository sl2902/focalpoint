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

**Auth:** API key + registered email in query params
```
?key={ACLED_API_KEY}&email={ACLED_EMAIL}
```

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
/acled/read?key=KEY&email=EMAIL&country=Palestine&limit=20&page=1
&fields=event_id_cnty|event_date|event_type|fatalities|latitude
|longitude|notes&event_date_where=BETWEEN&event_date=2026-04-01|2026-04-23
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

**Base URL:** https://cpj.org/data-api/

**Auth:** API key (beta — register at cpj.org)

**Key fields used:**
- id — incident identifier (used as source citation)
- date — date of incident
- country — country where incident occurred
- type — killed, imprisoned, missing, attacked
- gender — journalist gender
- medium — print, online, television, radio
- employment — staff or freelance
- local_or_foreign — local or foreign correspondent

**Primary use:**
Historical journalist incident rate per country.
Used as one input to severity scoring.
Not a real-time feed — query once per day and cache.

**Query approach:**
Fetch all incidents for a country for the past 5 years.
Calculate incidents-per-year rate for severity baseline.

**Pydantic model:** backend/ingestion/cpj_connector.py → CpjIncident

**Redis key pattern:** cpj:{country}
**TTL:** 86400 seconds (24 hours)

---

## RSF (Reporters Without Borders) Press Freedom Index

**Source:** https://rsf.org/en/index
**Format:** CSV download — no live API

**Key fields used:**
- country — country name
- score — press freedom score 0-100 (higher = more free)
- rank — global ranking out of 180 countries
- safety_score — journalist safety sub-score specifically

**Primary use:**
Static country-level press freedom baseline.
Loaded once at backend startup, stored in Redis.
Used as context for severity scoring — a region with low RSF
score gets elevated baseline severity.

**Score interpretation:**
- 75-100: Good — baseline GREEN
- 50-74: Satisfactory — baseline AMBER
- 25-49: Difficult — baseline RED modifier
- 0-24: Very serious — baseline CRITICAL modifier

**Pydantic model:** backend/ingestion/rsf_connector.py → RsfCountry

**Redis key pattern:** rsf:{country}
**TTL:** 86400 seconds (24 hours)

---

## Environment Variables Required

```
ACLED_API_KEY=
ACLED_EMAIL=
CPJ_API_KEY=
MAPBOX_TOKEN=
GEMMA_API_KEY=
REDIS_URL=
```

Never commit .env to git.
Add .env to .gitignore before first commit.
