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

from google import genai
from google.genai import types as genai_types
from loguru import logger

from backend.config import settings
from backend.security.output_validator import AlertOutput, validate_output

# Model ID for the 26B backend tier (see CLAUDE.md)
_BACKEND_MODEL = "gemma-4-26b-a4b-it"

# Strip markdown code fences that the model may accidentally emit.
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# Generation config: temperature 0 for deterministic safety assessments.
_GENERATION_CONFIG = genai_types.GenerateContentConfig(
    temperature=0.0,
    max_output_tokens=1024,
    response_mime_type="application/json",
)


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
        self._client = genai.Client(api_key=key)

    def generate_alert(self, prompt: str, region: str) -> AlertOutput:
        """
        Send *prompt* to Gemma 4 26B and return a validated AlertOutput.

        On any failure — API error, JSON parse error, or schema validation
        failure — logs a warning and returns the safe INSUFFICIENT_DATA
        fallback so the caller always receives a well-formed AlertOutput.

        Args:
            prompt: Fully assembled prompt from prompt_builder.build_prompt.
            region: Region label threaded into the fallback AlertOutput.

        Returns:
            Validated AlertOutput. Never raises.
        """
        try:
            response = self._client.models.generate_content(
                model=_BACKEND_MODEL,
                contents=prompt,
                config=_GENERATION_CONFIG,
            )
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

        try:
            raw_dict = _extract_json(raw_text)
        except json.JSONDecodeError as exc:
            logger.warning(
                f"gemma_client: JSON parse failed for region={region!r} — {exc}"
            )
            return _fallback(region)

        # validate_output handles Pydantic validation and returns its own
        # fallback on failure — no further exception handling needed here.
        return validate_output(raw_dict, region)


def _fallback(region: str) -> AlertOutput:
    """Return an INSUFFICIENT_DATA AlertOutput via validate_output's fallback path."""
    from datetime import datetime

    return validate_output(
        {
            "severity": "INSUFFICIENT_DATA",
            "summary": "Gemma 4 API call failed — safe fallback response.",
            "source_citations": ["FALLBACK:api-error"],
            "region": region,
            "timestamp": datetime.utcnow().isoformat(),
        },
        region,
    )
