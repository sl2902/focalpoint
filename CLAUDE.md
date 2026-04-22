# FocalPoint

## What
Real-time conflict intelligence tool for field journalists.
Aggregates live conflict and press safety data into severity-graded
alerts with an interactive map. Gemma 4 handles multilingual
reasoning and safety assessments. Mobile-first, edge-deployable.

Built for the Gemma 4 Good Hackathon — Global Resilience track.
Deadline: May 18, 2026.

## Why
Journalists operating in conflict zones lack real-time, structured
safety intelligence. FocalPoint bridges that gap — proactively
pushing severity-graded alerts rather than waiting to be queried.

## Stack
- Backend: Python 3.11, FastAPI, Pydantic v2, pytest, Redis, slowapi
- Mobile: React Native, Expo SDK 52, Expo SQLite
- AI: Gemma 4 (E2B/E4B on-device, 26B via backend)
- Data: ACLED API, GDELT 2.0, CPJ API, RSF Index
- Maps: Mapbox
- Deployment: Backend on Google Cloud Run, Expo for mobile

## Project Structure
See docs/architecture.md for full system design.
See docs/data-sources.md for API endpoints and field mappings.
See docs/caching.md for caching strategy.
See docs/security.md for guardrails and input validation.

backend/
  api/          # FastAPI routes
  ingestion/    # One file per data source connector
  processors/   # Gemma 4 reasoning and alert generation
  alerts/       # Severity scoring logic
  security/     # Input sanitisation, rate limiting, output validation
  tests/        # pytest — each connector tested independently
mobile/
  screens/      # Feed, Map, AlertDetail, Explore, Settings
  components/   # Reusable UI elements
  services/     # API calls to backend only — never to data APIs directly
docs/

## Model Routing Strategy
- E2B/E4B: on-device, handles quick queries and offline mode
- 26B backend: complex multi-source reasoning and alert generation
- Route based on connectivity status and query complexity
- On-device context: max 10 ACLED events + 5 GDELT articles
- Backend context: max 20 ACLED events + 10 GDELT articles
- This satisfies the Cactus special technology prize criteria

## Severity Levels
- GREEN: Normal activity, no immediate threat signals
- AMBER: Elevated conflict activity, monitor closely
- RED: Active incidents near watch zone, restrict movement
- CRITICAL: Imminent danger signals, evacuate or shelter

## Key Features
1. Proactive severity-graded alert feed — not query-first
2. Incident markers on map — tap to expand alert detail
3. Watch zone — journalist pins their operating region
4. Voice + text responses — always both outputs, never audio only
5. Discreet mode — dark screen, silent alerts, vibration only
6. Offline cache — last 100 alerts per watch zone persist locally
7. Cache timestamp always visible — stale data clearly labelled

## Context Grounding
- Gemma 4 must only reason from retrieved data — never free generation
- Every alert must cite source: ACLED event ID, GDELT URL, or CPJ ID
- If insufficient data exists, output "insufficient data" explicitly
- Cursor-based pagination on all data source queries
- All retrieval logic lives in backend/ingestion/ only

## Package Management
- Use uv exclusively — never pip directly
- Dependencies declared in pyproject.toml
- uv.lock committed to git for reproducible builds
- To install: uv sync
- To add a dependency: uv add {package}
- To add a dev dependency: uv add --dev {package}
- To run backend: uv run uvicorn backend.api.main:app --reload
- To run tests: uv run pytest
- Virtual environment managed by uv automatically
- Never create requirements.txt — pyproject.toml is the source of truth

## Critical Rules
- Never commit API keys — use .env always
- All Gemma 4 calls go through backend/processors/ only
- Mobile never calls data APIs directly — always via backend
- Each data connector must have independent pytest tests
- Pydantic schemas required for every data model — input and output
- Backend and mobile are completely decoupled
- No user input ever reaches data APIs or Gemma 4 unvalidated
- Do not bundle multiple unrelated changes in one commit
- Run pytest after every backend change before moving on
