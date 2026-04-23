# FocalPoint

Real-time conflict intelligence for field journalists. Aggregates live
conflict and press safety data into severity-graded alerts with an
interactive map. Built for the Gemma 4 Good Hackathon — Global Resilience track.

See [docs/architecture.md](docs/architecture.md) for full system design.

---

## Setup

```bash
cp .env.example .env   # fill in API credentials
uv sync
```

## Running the backend

```bash
uv run uvicorn backend.api.main:app --reload
```

## Running tests

```bash
uv run python -m pytest
```

All tests are fully isolated — no network calls, no Redis, no API keys required.

---

## backend/scripts/

Manual scripts for verifying live integrations. These are **not** part of the
automated test suite and require real API credentials in `.env`.

| Script | What it does |
|--------|--------------|
| `smoke_test.py` | Fetches 5 live ACLED events for Palestine and 5 GDELT articles for "conflict Gaza", runs the severity scorer on the combined results, and prints a human-readable breakdown including severity level, score, confidence, and per-component scores. |

### Running the smoke test

```bash
uv run python backend/scripts/smoke_test.py
```

Requires `ACLED_USERNAME` and `ACLED_PASSWORD` in `.env` (register at
[acleddata.com](https://acleddata.com)). GDELT, CPJ, and RSF require no
credentials and will run regardless.
