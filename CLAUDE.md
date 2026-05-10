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
- Mobile: React Native, Expo SDK 54, expo-audio, expo-speech-recognition, Expo SQLite
- AI: Gemma 4 via Gemini API (GOOGLE_AI_STUDIO_API_KEY) — model IDs:
       gemma-4-26b-a4b-it (backend alert generation),
       google/gemma-4-E4B-it (local transcription via transformers + MPS/CPU)
- ML deps (backend): transformers, torch, torchvision, librosa, accelerate, pillow
- Data: GDELT Cloud conflict events (GDELT_CLOUD_API_KEY), GDELT 2.0 Doc API
         news sentiment (no auth), CPJ local CSV at
         backend/data/cpj_incidents.csv, RSF hardcoded dict at
         backend/data/rsf_scores.py (180 countries, update annually)
         Note: ACLED connector preserved as acled_connector_disabled.py —
         reactivate if API access is granted
- Maps: MapLibre React Native + OpenStreetMap demo tiles (no key required)
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
- E4B local (backend): voice transcription via backend/processors/local_transcriber.py
  Loaded at startup, runs on MPS (Apple Silicon) or CPU. Returns HTTP 503 if unavailable.
  Mobile falls back to iOS native speech recognition (expo-speech-recognition) on 503.
- E2B/E4B on-device: handles quick queries and offline mode (planned)
- 26B backend: complex multi-source reasoning and alert generation
- Route based on connectivity status and query complexity
- On-device context: max 10 GDELT Cloud events + 5 GDELT Doc API articles
- Backend context: max 20 GDELT Cloud events + 10 GDELT Doc API articles
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
- Every alert must cite source: GDELT Cloud event ID, GDELT Doc API URL, or CPJ ID
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

## Current Status (May 10, 2026)

### Backend — Complete (551 tests)
- GDELT Cloud + Doc, CPJ, RSF connectors
- Severity scoring with historical floor and max severity rule
- Gemma 4 26B via Google AI Studio API for alerts
- Local Gemma 4 E4B via Transformers for audio transcription (MPS on Apple Silicon)
- Background scheduler, SQLite cache, FastAPI routes
- Web search fallback via Gemma 4 when GDELT Doc fails
- Ollama path: /api/chat, assistant pre-fill "{", temperature=1/top_p=0.95/top_k=64,
  num_predict=2048, thinking-token fallback, _last_json_object, _recover_truncated_json
- Google AI Studio: thinking_budget=512 (both configs), max_output_tokens=2048 (standard config)

### Mobile — In Progress
- Feed screen — working, shows all 9 watch zones; stale-while-revalidate cold start (always fetches backend), 24h eviction
- Alert Detail — working, back button, refresh
- Map screen — individual markers per watch zone (no clustering), region name labels,
  +/- zoom and home button working; DISPLAY_OFFSETS for Gaza/Palestine/Israel overlap
- Settings screen — working, scroll fixed
- Query screen — voice transcription working (E4B local), text query working
- Voice UX — mic meter pending, audio chip UX fix pending

### Pending
- Ollama: production testing and validation (core path implemented)
- Cloud Run deployment
- Demo video recording
- Kaggle writeup

### Context for future sessions
- Gemma E4B model weights are downloaded and cached locally on the dev machine;
  /transcribe works without re-downloading. On a fresh machine, first startup
  will trigger a multi-GB download — expect delay before 503 clears.
- Audio architecture is intentionally split: /transcribe handles audio (local E4B
  or 503); /query receives text only and is cacheable. Never merge these paths.
- GDELT Cloud: Iran, Sudan, Myanmar, Yemen, Syria need has_fatalities filter
  omitted — see NO_FATALITIES_FILTER_COUNTRIES in config.py; they return 0
  results otherwise.
- On-device E2B/E4B inference in the mobile app is planned but not implemented.
  The architecture doc describes it but no mobile code exists for it yet.
- Show a plan before implementing non-trivial changes — user preference confirmed
  across multiple sessions.
- uv only, never pip — enforced; requirements.txt must never be created.
- Ollama assistant pre-fill: messages array ends with {"role":"assistant","content":"{"}.
  Model continues from "{" — suppresses preamble/thinking in content. Response brace
  is prepended back before _extract_json. num_predict=2048 (model only generates the body).
  temperature=1/top_p=0.95/top_k=64 — Gemma 4 defaults; pre-fill enforces JSON structure.
- Ollama thinking tokens: Gemma 4 may put JSON in message['thinking'] and leave
  message['content'] empty even with think=False. _last_json_object("{" + thinking)
  anchors on "severity" key (only at top level of AlertOutput), rfind("{") before it,
  then walks forward tracking brace depth — "{" prepended because pre-fill may have
  absorbed the opening brace from the thinking field too.
- Google AI Studio thinking_budget=512 in both _GENERATION_CONFIG and
  _WEB_SEARCH_GENERATION_CONFIG — minimal budget lets model plan JSON structure before
  outputting; budget=0 can conflict with response_schema enforcement.
- backend/tests/conftest.py has an autouse fixture forcing OLLAMA_ENABLED=False
  for all tests so Ollama integration tests don't interfere with the Google AI
  Studio test suite.
