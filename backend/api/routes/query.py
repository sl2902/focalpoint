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
    get_cpj_connector,
    get_gdelt_cloud_connector,
    get_gdelt_connector,
    get_redis,
)
from backend.api.schemas import QueryResponse, TranscribeResponse
from backend.data.rsf_scores import RSF_ALIASES, RSF_SCORES
from backend.ingestion.cpj_connector import CPJConnector
from backend.ingestion.gdelt_connector import GdeltConnector
from backend.ingestion.gdeltcloud_connector import GdeltCloudConnector
from backend.processors.alert_generator import AlertGenerator
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
) -> QueryResponse:
    """Accept a journalist's query (text, audio, or both) and return a grounded assessment."""
    if not text and not audio:
        raise HTTPException(status_code=422, detail="Either text or audio must be provided.")

    lang = _resolve_language(language, request)
    region = region.title()

    sanitised = sanitise_query(text) if text else None
    query_text = sanitised.text if sanitised else f"journalist safety {region} current situation"

    audio_bytes: bytes | None = None
    audio_mime_type: str | None = None
    if audio:
        audio_bytes = await audio.read()
        audio_mime_type = audio.content_type or "audio/wav"
        logger.info(
            f"query: audio input received for region={region!r}"
            f" size={len(audio_bytes)}B mime={audio_mime_type!r} lang={lang!r}"
        )

    events = await gdelt_cloud.fetch_events(region)
    gdelt_resp = await gdelt.fetch_articles(query_text)
    cpj_stats = cpj.get_country_stats(region)
    rsf_key = RSF_ALIASES.get(region, region)
    rsf_score = RSF_SCORES.get(rsf_key, 0.0)

    use_web_search = len(gdelt_resp.articles) == 0
    logger.debug(
        f"query: region={region!r} gdelt_articles={len(gdelt_resp.articles)}"
        f" use_web_search={use_web_search}"
    )
    audio_provided = audio_bytes is not None

    # Cache check — only for text-only, GDELT-backed responses.
    if not use_web_search and not audio_provided and redis is not None:
        key = _cache_key(region, query_text)
        try:
            cached = await redis.get(key)
            if cached:
                logger.debug(f"query: cache hit for key={key!r}")
                data = json.loads(cached)
                data["was_sanitised"] = sanitised.was_modified if sanitised else False
                return QueryResponse(**data)
        except Exception as exc:
            logger.warning(f"query: Redis read failed — {exc}")

    alert = generator.generate(
        conflict_events=events,
        gdelt_articles=gdelt_resp.articles,
        gdelt_aggregate_tone=gdelt_resp.aggregate_tone,
        cpj_stats=cpj_stats,
        rsf_score=rsf_score,
        region=region,
        journalist_query=query_text,
        audio_bytes=audio_bytes,
        audio_mime_type=audio_mime_type,
    )

    response = QueryResponse(
        answer=alert.summary,
        severity=alert.severity,
        source_citations=alert.source_citations,
        region=alert.region,
        timestamp=alert.timestamp,
        was_sanitised=sanitised.was_modified if sanitised else False,
    )

    # Cache write — only for text-only, GDELT-backed responses.
    if not use_web_search and not audio_provided and redis is not None:
        key = _cache_key(region, query_text)
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
    generator: AlertGenerator = Depends(get_alert_generator),
) -> TranscribeResponse:
    """Transcribe an audio file and return the text for display in the mobile query field."""
    lang = _resolve_language(language, request)
    audio_bytes = await audio.read()
    mime_type = audio.content_type or "audio/wav"
    logger.info(
        f"transcribe: audio received size={len(audio_bytes)}B"
        f" mime={mime_type!r} lang={lang!r}"
    )
    text = generator.transcribe(audio_bytes, mime_type, lang)
    return TranscribeResponse(text=text, language=lang)
