"""
Gemma 4 API client for FocalPoint backend.

Wraps the google-genai SDK to call gemma-4-26b-a4b-it. Parses the model's
JSON response and validates it through output_validator.validate_output
before returning — raw model text is never surfaced to the API layer.

Design decisions:
- JSON extraction uses a lenient regex strip so accidental markdown fences
  from the model do not break parsing.
- On any error (API failure, JSON parse error, validation failure) the
  client returns the safe INSUFFICIENT_DATA fallback from validate_output
  rather than propagating an exception, keeping the API layer stateless.
- All exceptions are logged with context but without the raw prompt text
  (which may contain journalist-sensitive location data).
"""

from __future__ import annotations

import json
import re

import time

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from loguru import logger

from backend.config import settings
from backend.security.output_validator import AlertOutput, validate_output

# Model ID for the 26B backend tier (see CLAUDE.md)
_BACKEND_MODEL = "gemma-4-26b-a4b-it"

# Strip markdown code fences that the model may accidentally emit.
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# Response schema that constrains Gemma 4 output to exactly the AlertOutput
# structure. Enforced at the API level before our Pydantic validator runs.
_ALERT_RESPONSE_SCHEMA = genai_types.Schema(
    type=genai_types.Type.OBJECT,
    properties={
        "severity": genai_types.Schema(
            type=genai_types.Type.STRING,
            enum=["GREEN", "AMBER", "RED", "CRITICAL", "INSUFFICIENT_DATA"],
        ),
        "summary": genai_types.Schema(
            type=genai_types.Type.STRING,
        ),
        "source_citations": genai_types.Schema(
            type=genai_types.Type.ARRAY,
            items=genai_types.Schema(
                type=genai_types.Type.OBJECT,
                properties={
                    "id": genai_types.Schema(type=genai_types.Type.STRING),
                    "description": genai_types.Schema(type=genai_types.Type.STRING),
                },
                required=["id", "description"],
            ),
        ),
        "region": genai_types.Schema(
            type=genai_types.Type.STRING,
        ),
        "timestamp": genai_types.Schema(
            type=genai_types.Type.STRING,
        ),
    },
    required=["severity", "summary", "source_citations", "region", "timestamp"],
)

# Generation config: temperature 0 for deterministic safety assessments.
# response_schema enforces AlertOutput structure at the API level.
_GENERATION_CONFIG = genai_types.GenerateContentConfig(
    temperature=0.0,
    max_output_tokens=512,
    response_mime_type="application/json",
    response_schema=_ALERT_RESPONSE_SCHEMA,
)

# Web search config: same temperature but includes the Google Search grounding
# tool. response_mime_type is omitted — it is incompatible with tool use.
_WEB_SEARCH_GENERATION_CONFIG = genai_types.GenerateContentConfig(
    temperature=0.0,
    max_output_tokens=1024,
    tools=[{"google_search": {}}],
)

# Transcription config: plain text response, no JSON schema.
_TRANSCRIBE_CONFIG = genai_types.GenerateContentConfig(
    temperature=0.0,
    max_output_tokens=512,
)


def _extract_grounding_urls(response) -> list[tuple[str, str]]:
    """
    Pull real source URLs out of the grounding metadata on a Gemini response.

    Returns a list of (uri, title) pairs. The grounding_chunks field contains
    the actual publisher URLs (e.g. reuters.com/...) — distinct from the
    vertexaisearch.cloud.google.com redirect URLs the model uses internally.
    Returns an empty list on any error or when metadata is absent.
    """
    try:
        candidates = response.candidates or []
        if not candidates:
            return []
        metadata = getattr(candidates[0], "grounding_metadata", None)
        if not metadata:
            return []
        chunks = getattr(metadata, "grounding_chunks", None) or []
        result: list[tuple[str, str]] = []
        for chunk in chunks:
            web = getattr(chunk, "web", None)
            if not web:
                continue
            uri = getattr(web, "uri", None)
            title = getattr(web, "title", None) or uri
            if uri and "vertexaisearch.cloud.google.com" not in uri:
                result.append((uri, str(title)))
        logger.debug(f"grounding: extracted {len(result)} real URLs from metadata")
        return result
    except Exception as exc:
        logger.debug(f"grounding: could not extract URLs — {exc}")
        return []


def _extract_json(raw_text: str) -> dict:
    """
    Strip optional markdown fences and parse the remaining text as JSON.

    Raises json.JSONDecodeError if the text is not valid JSON after stripping.
    """
    cleaned = _JSON_FENCE_RE.sub("", raw_text).strip()
    return json.loads(cleaned)


class GemmaClient:
    """
    Synchronous Gemma 4 26B client using the google-genai SDK.

    Instantiate once per backend process (API key is read from settings
    at construction time). Pass the instance to alert_generator.
    """

    def __init__(self, api_key: str | None = None) -> None:
        key = api_key or settings.GOOGLE_AI_STUDIO_API_KEY
        self._client = genai.Client(api_key=key, http_options={"timeout": 120_000})  # milliseconds

    def generate_alert(
        self,
        prompt: str,
        region: str,
        use_web_search: bool = False,
        audio_bytes: bytes | None = None,
        audio_mime_type: str | None = None,
    ) -> AlertOutput:
        """
        Send *prompt* to Gemma 4 26B and return a validated AlertOutput.

        On any failure — API error, JSON parse error, or schema validation
        failure — logs a warning and returns the safe INSUFFICIENT_DATA
        fallback so the caller always receives a well-formed AlertOutput.

        Args:
            prompt:          Fully assembled prompt from prompt_builder.build_prompt.
            region:          Region label threaded into the fallback AlertOutput.
            use_web_search:  When True, enables the Google Search grounding tool so
                             the model can fetch live sources when GDELT Doc API has
                             no usable articles.
            audio_bytes:     Raw audio bytes for multimodal input. When provided,
                             Gemma receives both the audio and the text prompt.
            audio_mime_type: MIME type of the audio (e.g. "audio/wav", "audio/mp4").

        Returns:
            Validated AlertOutput. Never raises.
        """
        config = _WEB_SEARCH_GENERATION_CONFIG if use_web_search else _GENERATION_CONFIG

        # Build contents — multimodal list when audio provided, plain string otherwise.
        if audio_bytes and audio_mime_type:
            contents: list | str = [
                genai_types.Part(
                    inline_data=genai_types.Blob(data=audio_bytes, mime_type=audio_mime_type)
                ),
                genai_types.Part(text=prompt),
            ]
        else:
            contents = prompt

        try:
            response = self._client.models.generate_content(
                model=_BACKEND_MODEL,
                contents=contents,
                config=config,
            )
        except httpx.RemoteProtocolError as exc:
            logger.warning(
                f"gemma_client: RemoteProtocolError for region={region!r}, retrying — {exc}"
            )
            try:
                response = self._client.models.generate_content(
                    model=_BACKEND_MODEL,
                    contents=contents,
                    config=config,
                )
            except Exception as exc2:
                logger.warning(
                    f"gemma_client: retry after RemoteProtocolError failed for"
                    f" region={region!r} — {type(exc2).__name__}: {exc2}"
                )
                return _fallback(region)
        except genai_errors.ServerError as exc:
            # 5xx from the Gemini API (commonly 504 DEADLINE_EXCEEDED under load).
            # Sleep 3 s before retrying — immediate retry hits the same overloaded
            # backend. One retry only; if it fails again return the safe fallback.
            logger.warning(
                f"gemma_client: ServerError for region={region!r}, retrying in 3 s — {exc}"
            )
            time.sleep(3)
            try:
                response = self._client.models.generate_content(
                    model=_BACKEND_MODEL,
                    contents=contents,
                    config=config,
                )
            except Exception as exc2:
                logger.warning(
                    f"gemma_client: retry after ServerError failed for"
                    f" region={region!r} — {type(exc2).__name__}: {exc2}"
                )
                return _fallback(region)
        except Exception as exc:
            logger.warning(
                f"gemma_client: API call failed for region={region!r} — {type(exc).__name__}: {exc}"
            )
            return _fallback(region)

        raw_text = response.text
        if not raw_text:
            logger.warning(
                f"gemma_client: empty response from model for region={region!r}"
            )
            return _fallback(region)

        logger.debug(f"gemma_client: raw response for region={region!r} — {raw_text!r}")

        # Extract real publisher URLs from grounding metadata while we still
        # have the response object — these replace the expiring redirect URLs
        # that the model embeds in its citation ids.
        grounding_urls = _extract_grounding_urls(response) if use_web_search else []

        try:
            raw_dict = _extract_json(raw_text)
        except json.JSONDecodeError as exc:
            logger.warning(
                f"gemma_client: JSON parse failed for region={region!r} — {exc}"
            )
            if use_web_search:
                # Web-search responses are prose, not JSON. Pass the grounded
                # text (plus real URLs from metadata) to a second JSON-constrained
                # call so we always get a typed AlertOutput.
                logger.info(
                    f"gemma_client: structuring web-search prose for region={region!r}"
                )
                return self._structure_web_response(raw_text, region, grounding_urls)
            return _fallback(region)

        result = validate_output(raw_dict, region)

        if result.severity == "INSUFFICIENT_DATA":
            logger.warning(
                f"gemma_client: INSUFFICIENT_DATA for region={region!r} — retrying once"
            )
            try:
                retry_response = self._client.models.generate_content(
                    model=_BACKEND_MODEL,
                    contents=contents,
                    config=config,
                )
                retry_text = retry_response.text
                if retry_text:
                    logger.debug(f"gemma_client: retry raw response for region={region!r} — {retry_text!r}")
                    retry_grounding_urls = (
                        _extract_grounding_urls(retry_response) if use_web_search else []
                    ) or grounding_urls
                    if use_web_search:
                        try:
                            result = validate_output(_extract_json(retry_text), region)
                        except json.JSONDecodeError:
                            result = self._structure_web_response(
                                retry_text, region, retry_grounding_urls
                            )
                    else:
                        result = validate_output(_extract_json(retry_text), region)
            except Exception as exc:
                logger.warning(
                    f"gemma_client: retry failed for region={region!r} — {type(exc).__name__}: {exc}"
                )

        return result

    def _structure_web_response(
        self,
        grounded_text: str,
        region: str,
        grounding_urls: list[tuple[str, str]] | None = None,
    ) -> AlertOutput:
        """
        Convert free-form web-search prose into a structured AlertOutput.

        Called when generate_alert's web-search response is not JSON-parseable.
        Passes the grounded text (plus real publisher URLs from grounding metadata)
        to a second JSON-constrained call so we always get a typed AlertOutput.
        Falls back to _fallback(region) if the second call also fails.

        Args:
            grounded_text:  Free-form prose from the web-search response.
            region:         Region label for the fallback path.
            grounding_urls: Real (uri, title) pairs from grounding_metadata.
                            Injected into the prompt so the model cites permanent
                            publisher URLs instead of expiring redirect URLs.
        """
        urls = grounding_urls or []
        if urls:
            sources_block = "\n\nVerified source URLs (use these exact URLs as citation ids):\n"
            for uri, title in urls[:10]:
                sources_block += f"  - {uri}  ({title})\n"
        else:
            sources_block = ""

        structure_prompt = (
            f"You have gathered the following live intelligence about journalist "
            f"safety in {region}:\n\n"
            f"{grounded_text[:3000]}"
            f"{sources_block}\n\n"
            "Based solely on the intelligence above, produce your JSON safety assessment. "
            "For each citation, use one of the verified source URLs above as the id field "
            "if the source is listed; otherwise use CPJ:<detail> or RSF:<detail>. "
            "Never use vertexaisearch.cloud.google.com URLs."
        )
        try:
            response = self._client.models.generate_content(
                model=_BACKEND_MODEL,
                contents=structure_prompt,
                config=_GENERATION_CONFIG,
            )
            text = response.text
            if not text:
                logger.warning(
                    f"gemma_client: _structure_web_response got empty response for region={region!r}"
                )
                return _fallback(region)
            logger.debug(
                f"gemma_client: structured web response for region={region!r} — {text!r}"
            )
            return validate_output(_extract_json(text), region)
        except Exception as exc:
            logger.warning(
                f"gemma_client: _structure_web_response failed for region={region!r}"
                f" — {type(exc).__name__}: {exc}"
            )
            return _fallback(region)

    def transcribe_audio(
        self,
        audio_bytes: bytes,
        mime_type: str,
        language: str = "en",
    ) -> str:
        """
        Transcribe audio using Gemma 4 and return the transcript as plain text.

        Args:
            audio_bytes: Raw audio bytes.
            mime_type:   MIME type of the audio (e.g. "audio/wav", "audio/mp4").
            language:    BCP-47 language hint (e.g. "en", "fr", "ar").

        Returns:
            Transcribed text, or empty string on any failure. Never raises.
        """
        prompt = (
            f"Transcribe the following audio recording exactly as spoken. "
            f"The expected language is '{language}'. "
            "Return only the transcribed text with no additional commentary, "
            "labels, or formatting."
        )
        contents = [
            genai_types.Part(
                inline_data=genai_types.Blob(data=audio_bytes, mime_type=mime_type)
            ),
            genai_types.Part(text=prompt),
        ]
        try:
            response = self._client.models.generate_content(
                model=_BACKEND_MODEL,
                contents=contents,
                config=_TRANSCRIBE_CONFIG,
            )
            text = (response.text or "").strip()
            if not text:
                logger.warning("gemma_client: transcribe_audio returned empty response")
            return text
        except Exception as exc:
            logger.warning(
                f"gemma_client: transcribe_audio failed — {type(exc).__name__}: {exc}"
            )
            return ""


def _fallback(region: str) -> AlertOutput:
    """Return an INSUFFICIENT_DATA AlertOutput via validate_output's fallback path."""
    from datetime import datetime

    return validate_output(
        {
            "severity": "INSUFFICIENT_DATA",
            "summary": "Gemma 4 API call failed — safe fallback response.",
            "source_citations": [{"id": "FALLBACK:api-error", "description": "Gemma 4 API call failed"}],
            "region": region,
            "timestamp": datetime.utcnow().isoformat(),
        },
        region,
    )
