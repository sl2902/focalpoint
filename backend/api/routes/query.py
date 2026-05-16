"""Query endpoint.

POST /query — accepts a journalist's natural language query (text and/or audio),
fetches live conflict data, and returns a grounded Gemma 4 assessment.

POST /transcribe — accepts an audio file and returns only the transcription so
the mobile app can populate the text field before submission.

Both endpoints accept multipart/form-data. The Accept-Language header is used
as the language hint when the language form field is not explicitly provided.

Caching strategy
----------------
Responses backed by GDELT data (use_web_search=False, no audio) are cached in
Redis with key pattern ``query:{region}:{query_hash}`` and TTL of 3600 seconds.
Audio queries and web-search-backed responses are never cached — audio content
is ephemeral and live web results are time-sensitive.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from loguru import logger

from backend.api.dependencies import (
    get_alert_generator,
    get_alerts_db_path,
    get_cpj_connector,
    get_gdelt_cloud_connector,
    get_gdelt_connector,
    get_redis,
)
from backend.scheduler import store
from backend.api.schemas import QueryResponse, TranscribeResponse
from backend.config import settings
from backend.data.rsf_scores import RSF_ALIASES, RSF_SCORES
from backend.ingestion.cpj_connector import CPJConnector
from backend.ingestion.gdelt_connector import GdeltConnector
from backend.ingestion.gdeltcloud_connector import GdeltCloudConnector
from backend.alerts.severity_scorer import score_severity
from backend.processors.alert_generator import AlertGenerator
from backend.processors.local_transcriber import TranscriptionUnavailableError, get_local_transcriber
from backend.security.rate_limiter import QUERY_RATE_LIMIT, limiter
from backend.security.sanitiser import sanitise_query

_CACHE_TTL = 3600  # seconds

router = APIRouter(tags=["query"])

_LANG_RE = re.compile(r"^[a-z]{2}$")


def _cache_key(region: str, query_text: str) -> str:
    query_hash = hashlib.sha256(query_text.encode()).hexdigest()[:16]
    return f"query:{region}:{query_hash}"


def _parse_accept_language(header: str) -> str:
    """Extract the primary 2-letter language code from an Accept-Language header value."""
    primary = header.split(",")[0].split(";")[0].split("-")[0].strip().lower()
    return primary if _LANG_RE.match(primary) else "en"


def _resolve_language(form_language: str | None, request: Request) -> str:
    """Return the effective language: form field → Accept-Language header → 'en'."""
    if form_language:
        return form_language
    header = request.headers.get("Accept-Language", "")
    return _parse_accept_language(header) if header else "en"


@router.post("/query", response_model=QueryResponse)
@limiter.limit(QUERY_RATE_LIMIT)
async def query(
    request: Request,
    region: Annotated[str, Form(min_length=2, max_length=100)],
    text: Annotated[str | None, Form(min_length=1, max_length=500)] = None,
    language: Annotated[str | None, Form(pattern=r"^[a-z]{2}$")] = None,
    audio: Annotated[UploadFile | None, File()] = None,
    gdelt_cloud: GdeltCloudConnector = Depends(get_gdelt_cloud_connector),
    gdelt: GdeltConnector = Depends(get_gdelt_connector),
    cpj: CPJConnector = Depends(get_cpj_connector),
    generator: AlertGenerator = Depends(get_alert_generator),
    redis: Annotated[aioredis.Redis | None, Depends(get_redis)] = None,
    db_path: str = Depends(get_alerts_db_path),
) -> QueryResponse:
    """Accept a journalist's query (text, audio, or both) and return a grounded assessment."""
    if not text and not audio:
        raise HTTPException(status_code=422, detail="Either text or audio must be provided.")

    lang = _resolve_language(language, request)
    region = region.title()

    sanitised = sanitise_query(text) if text else None
    # journalist_query goes to Gemma 4 as context only — never used as a data API search term.
    journalist_query = sanitised.text if sanitised else f"journalist safety {region} current situation"

    logger.debug(f"query: received text={journalist_query!r} region={region!r} lang={lang!r}")
    if sanitised and sanitised.was_modified:
        logger.debug(
            f"query: text modified by sanitiser"
            f" — original={text!r} sanitised={journalist_query!r}"
        )

    if audio:
        audio_bytes = await audio.read()
        logger.info(
            f"query: audio field received but ignored for generation"
            f" size={len(audio_bytes)}B — transcription handled by /transcribe"
        )

    # Cache check before any data-source calls — journalist_query and region are
    # already known. Cached entries are only written for GDELT-backed responses, so
    # a hit is always valid regardless of what GDELT would return today.
    if redis is not None:
        key = _cache_key(region, journalist_query)
        try:
            cached = await redis.get(key)
            if cached:
                logger.debug(f"query: cache hit for key={key!r} — skipping GDELT fetches")
                data = json.loads(cached)
                data["was_sanitised"] = sanitised.was_modified if sanitised else False
                return QueryResponse(**data)
        except Exception as exc:
            logger.warning(f"query: Redis read failed — {exc}")

    # Cache miss — fetch live data.
    # GDELT Doc API search is always region-scoped, not journalist-question-scoped.
    events = await gdelt_cloud.fetch_events(region)
    gdelt_resp = await gdelt.fetch_articles_for_region(region, cache_ttl=86400)
    cpj_stats = cpj.get_country_stats(region)
    rsf_key = RSF_ALIASES.get(region, region)
    rsf_score = RSF_SCORES.get(rsf_key, 0.0)

    cached_alert = await store.get_cached_alert(db_path, region, days=1)
    is_fallback_alert = cached_alert is not None and any(
        c.id.startswith("FALLBACK:") for c in cached_alert.source_citations
    )
    previous_assessment = (
        cached_alert.summary
        if cached_alert is not None and not is_fallback_alert
        else None
    )
    logger.debug(
        f"query: previous_assessment for {region!r} — "
        + ("injected into prompt" if previous_assessment else "none (cache miss or fallback)")
    )

    use_web_search = len(gdelt_resp.articles) == 0
    # Ollama path: cap articles at 3 to keep the prompt under ~1500 tokens.
    # Scorer still uses the full article list — it's deterministic and cheap.
    gdelt_articles = gdelt_resp.articles[:3] if settings.OLLAMA_ENABLED else gdelt_resp.articles
    logger.debug(
        f"query: region={region!r} gdelt_articles={len(gdelt_resp.articles)}"
        f" (using {len(gdelt_articles)} for generator)"
        f" use_web_search={use_web_search}"
    )

    severity_result = score_severity(
        conflict_events=events,
        gdelt_articles=gdelt_resp.articles,
        cpj_stats=cpj_stats,
        rsf_press_freedom=rsf_score,
        gdelt_aggregate_tone=gdelt_resp.aggregate_tone,
        region=region,
    )
    logger.debug(
        f"query: scorer result for {region!r} — {severity_result.level.value}"
        f" (score={severity_result.score:.1f}, floor={severity_result.floor_applied})"
    )

    alert = generator.generate(
        conflict_events=events,
        gdelt_articles=gdelt_articles,
        gdelt_aggregate_tone=gdelt_resp.aggregate_tone,
        cpj_stats=cpj_stats,
        rsf_score=rsf_score,
        region=region,
        journalist_query=journalist_query,
        severity_result=severity_result,
        previous_assessment=previous_assessment,
    )

    response = QueryResponse(
        answer=alert.summary,
        severity=alert.severity,
        source_citations=alert.source_citations,
        region=alert.region,
        timestamp=alert.timestamp,
        was_sanitised=sanitised.was_modified if sanitised else False,
    )

    # Cache write — GDELT-backed, real responses only.
    # Exclusions:
    #   - INSUFFICIENT_DATA severity: may be a transient API timeout/failure.
    #   - FALLBACK citations (id starts with "FALLBACK:"): _apply_max_severity can
    #     promote a Gemma API-failure alert from INSUFFICIENT_DATA to a real severity
    #     using the deterministic scorer, which would otherwise pass the severity check
    #     while leaving "Gemma 4 API call failed" as the summary. Never cache those.
    is_fallback = any(
        c.id.startswith("FALLBACK:") for c in alert.source_citations
    )
    if not use_web_search and redis is not None and alert.severity != "INSUFFICIENT_DATA" and not is_fallback:
        key = _cache_key(region, journalist_query)
        try:
            payload = response.model_dump(mode="json")
            await redis.setex(key, _CACHE_TTL, json.dumps(payload))
            logger.debug(f"query: cached response under key={key!r} ttl={_CACHE_TTL}s")
        except Exception as exc:
            logger.warning(f"query: Redis write failed — {exc}")

    return response


@router.post("/transcribe", response_model=TranscribeResponse)
@limiter.limit(QUERY_RATE_LIMIT)
async def transcribe(
    request: Request,
    audio: Annotated[UploadFile, File()],
    language: Annotated[str | None, Form(pattern=r"^[a-z]{2}$")] = None,
) -> TranscribeResponse:
    """Transcribe an audio file using the local Gemma 4 E4B model.

    Returns HTTP 503 if the local model is unavailable — the mobile client
    should fall back to device-native speech recognition in that case.
    """
    lang = _resolve_language(language, request)
    audio_bytes = await audio.read()
    mime_type = audio.content_type or "audio/wav"
    logger.info(
        f"transcribe: audio received size={len(audio_bytes)}B"
        f" mime={mime_type!r} lang={lang!r}"
    )
    try:
        local = get_local_transcriber()
        text = local.transcribe(audio_bytes, mime_type, lang)
    except TranscriptionUnavailableError as exc:
        logger.warning(f"transcribe: local model unavailable — {exc}")
        raise HTTPException(status_code=503, detail="local_transcription_unavailable")
    return TranscribeResponse(text=text, language=lang)
