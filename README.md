# FocalPoint

Real-time conflict intelligence for field journalists. Aggregates live
conflict and press safety data into severity-graded alerts with an
interactive map. Built for the Gemma 4 Good Hackathon — Global Resilience track.

See [docs/architecture.md](docs/architecture.md) for full system design.

---

## Prerequisites

- Python 3.11
- Node 20 (use nvm: `nvm use 20`)
- uv package manager: `pip install uv`
- Ollama: `brew install ollama`
- ffmpeg: `brew install ffmpeg`
- Docker (for Redis)
- Xcode 26+ with iOS simulator
- HuggingFace account (for Gemma 4 E4B weights)

## Environment Setup

Copy `.env.example` to `.env` and fill in:

- `GOOGLE_AI_STUDIO_API_KEY` — from [Google AI Studio](https://aistudio.google.com)
- `GDELT_CLOUD_API_KEY` — from gdeltcloud.com
- `HUGGINGFACE_TOKEN` — from huggingface.co/settings/tokens
- `OLLAMA_ENABLED=True` for local inference, `False` for Google AI Studio
- `OLLAMA_API_KEY` — from ollama.com/settings/keys (for web search)

## Running Locally

### Backend (required for both options)

1. `docker run -d -p 6379:6379 redis`
2. `brew services start ollama`
3. `ollama pull gemma4:26b`
   > **Warning:** downloads ~17 GB of model weights. Allow 10–30 minutes on first run depending on connection speed. The model is cached locally after the first download.
   >
   > **Skip this step** if using Google AI Studio instead — set `OLLAMA_ENABLED=False` in `.env` and the backend routes all inference through the API. No local model required.
4. `uv run uvicorn backend.api.main:app --reload`

---

## Option 1 — Backend + Swagger UI (Quickest)

No mobile setup needed. Once the backend is running, open the interactive API docs at:

**http://localhost:8000/docs**

Covers all endpoints: alerts feed, per-region assessments, natural language query, and audio transcription.

---

## Option 2 — Full Mobile App (Complete Experience)

Full React Native iOS app with MapLibre map, voice queries, and real-time alert feed. Requires Xcode, CocoaPods, and Node 20.

1. `cp mobile/.env.example mobile/.env` — then edit `mobile/.env` if targeting a physical device (see comment inside)
2. `brew install cocoapods`
3. `nvm use 20`
4. `cd mobile && npm install`
5. `npx expo prebuild --platform ios`
6. `cd ios && pod install && cd ..`
7. `npx expo start`

> **Physical device**: change `EXPO_PUBLIC_API_BASE_URL` in `mobile/.env` to your Mac's local IP (e.g. `http://192.168.x.x:8000`) so the app can reach the backend over your local network.

> **Note:** MapLibre native map requires a proper native build. Running via Expo Go will show a fallback web map instead.

---

### Seeding Data

```bash
for region in Palestine Gaza Israel Iran Ukraine Sudan Myanmar Yemen Syria; do
  curl -s "http://localhost:8000/alerts/$region?force=true&days=1"
  sleep 20
done
```

## Running Tests

```bash
uv run pytest backend/tests/ -q
```

---

## backend/scripts/

Manual scripts for verifying live integrations. These are **not** part of the
automated test suite and require real API credentials in `.env`.

| Script | What it does |
|--------|--------------|
| `smoke_test.py` | Fetches live GDELT Cloud conflict events for Palestine and GDELT Doc articles for "journalist Gaza", runs the severity scorer on the combined results, and prints a human-readable breakdown including severity level, score, confidence, and per-component scores. |

### Running the smoke test

```bash
uv run python backend/scripts/smoke_test.py
```

Requires `GDELT_CLOUD_API_KEY` in `.env`. GDELT Doc API, CPJ, and RSF require no credentials and will run regardless.

---

## License

MIT License. See [LICENSE](LICENSE) for details.
