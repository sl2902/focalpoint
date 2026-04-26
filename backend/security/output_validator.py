"""
Input validation and output validation — Layers 1 & 4 of the FocalPoint
security stack.

Layer 1 — Pydantic input schemas applied at every FastAPI endpoint.
  JournalistQuery  validates text queries before any processing.
  WatchZone        validates coordinates and radius from the mobile client.

Layer 4 — AlertOutput schema validates Gemma 4 responses before they
  reach the API layer. validate_output() wraps model parsing and returns
  a safe INSUFFICIENT_DATA fallback if validation fails, so raw model
  output is never surfaced to the mobile client.

All validation failures are logged (raw model output is redacted).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from loguru import logger
from pydantic import BaseModel, Field, ValidationError, field_validator

# ---------------------------------------------------------------------------
# Citation patterns for AlertOutput validation
# ---------------------------------------------------------------------------

# Matches any http/https URL with at least one non-whitespace character after.
_URL_RE = re.compile(r"^https?://\S+$")

# Matches GDELT Cloud event IDs: start with "conflict_" followed by word chars/hyphens.
# Example: conflict_20260423_001
_GDELT_CLOUD_ID_RE = re.compile(r"^conflict_[\w\-]+$")

# Matches CPJ and RSF historical-source citations used when no live event data
# is available.  Bare "CPJ" / "RSF" and namespaced variants ("CPJ:country-2024",
# "RSF:Press Freedom Index") are all accepted.
# Examples: "CPJ", "RSF", "CPJ:Syria-2024", "RSF:Press Freedom Index 2025"
_HISTORICAL_SOURCE_RE = re.compile(r"^(CPJ|RSF)(:.+)?$")

# Matches internal diagnostic fallback citations emitted by GemmaClient when the
# API call fails.  Format: "FALLBACK:<slug>" (lowercase slug, hyphens allowed).
# Example: "FALLBACK:api-error"
_FALLBACK_RE = re.compile(r"^FALLBACK:[a-z][a-z0-9-]+$")


# ---------------------------------------------------------------------------
# Layer 1 — Input schemas
# ---------------------------------------------------------------------------


class JournalistQuery(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    region: str = Field(min_length=2, max_length=100)
    language: str = Field(default="en", pattern="^[a-z]{2}$")


class WatchZone(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_km: float = Field(ge=1, le=500)
    label: str = Field(min_length=1, max_length=100)


# ---------------------------------------------------------------------------
# Layer 4 — Output schema
# ---------------------------------------------------------------------------


class Citation(BaseModel):
    id: str          # GDELT Cloud event ID (e.g. "conflict_50be6d52") or URL
    description: str # Human-readable: "Armed Clash — Gaza City, 2026-04-22 (5 fatalities)"


class AlertOutput(BaseModel):
    severity: Literal["GREEN", "AMBER", "RED", "CRITICAL", "INSUFFICIENT_DATA"]
    summary: str = Field(min_length=10, max_length=1000)
    source_citations: list[Citation] = Field(default_factory=list)
    region: str
    timestamp: datetime

    @field_validator("source_citations", mode="after")
    @classmethod
    def citations_must_be_real(cls, v: list[Citation], info) -> list[Citation]:
        """Strip invalid citation IDs rather than rejecting the whole response.

        Valid formats: http/https URL, GDELT Cloud event ID (conflict_*),
        CPJ bare or namespaced (CPJ / CPJ:<detail>),
        RSF bare or namespaced (RSF / RSF:<detail>).

        Invalid entries are removed with a warning log. A ValueError is raised
        only when every citation is invalid (and severity is not INSUFFICIENT_DATA),
        since an alert with zero sources cannot be trusted.
        """
        severity = (info.data or {}).get("severity")
        valid = []
        for citation in v:
            if (
                _URL_RE.match(citation.id)
                or _GDELT_CLOUD_ID_RE.match(citation.id)
                or _HISTORICAL_SOURCE_RE.match(citation.id)
                or _FALLBACK_RE.match(citation.id)
            ):
                valid.append(citation)
            else:
                logger.warning(
                    f"output_validator: stripping invalid citation id {citation.id!r}"
                )
        if severity != "INSUFFICIENT_DATA" and len(valid) == 0:
            raise ValueError(
                "source_citations contains no valid citation IDs — "
                "all entries were stripped or the list was empty"
            )
        return valid


# ---------------------------------------------------------------------------
# validate_output
# ---------------------------------------------------------------------------


_SUMMARY_MAX = 1000
_SUMMARY_SAFE = 900  # truncate target — leaves headroom for the validator


def _truncate_summary(text: str) -> str:
    """Truncate a runaway summary to the last complete sentence within _SUMMARY_SAFE chars."""
    if len(text) <= _SUMMARY_MAX:
        return text
    window = text[:_SUMMARY_SAFE]
    # Walk sentence terminators from the right so we keep the most content.
    for sep in (". ", "! ", "? "):
        pos = window.rfind(sep)
        if pos > 20:
            return window[: pos + 1].rstrip()
    # No sentence boundary — hard cut with ellipsis.
    return window.rstrip() + "…"


def validate_output(raw: dict, region: str) -> AlertOutput:
    """
    Validate a Gemma 4 response dict against AlertOutput.

    Pre-truncates the summary when the model produces a repetition loop
    (output > 1000 chars) so valid leading content is preserved rather
    than discarding the whole response. On any remaining ValidationError
    logs the failure and returns a safe fallback with severity=INSUFFICIENT_DATA.

    Args:
        raw:    Dict parsed from Gemma 4's JSON response.
        region: Watch zone region string, threaded into the fallback.
    """
    # Rescue repetition-loop responses by truncating before Pydantic sees them.
    if isinstance(raw.get("summary"), str) and len(raw["summary"]) > _SUMMARY_MAX:
        original_len = len(raw["summary"])
        raw = {**raw, "summary": _truncate_summary(raw["summary"])}
        logger.warning(
            f"output_validator: summary truncated {original_len} → {len(raw['summary'])} chars "
            f"for region={region!r} (repetition loop detected)"
        )
    logger.debug(f"output_validator: raw dict before validation — {raw!r}")
    try:
        return AlertOutput.model_validate(raw)
    except ValidationError as exc:
        logger.warning(
            f"output_validator: AlertOutput failed validation — {exc.error_count()} error(s): "
            f"{[e['msg'] for e in exc.errors()]} — returning fallback"
        )
        return AlertOutput.model_construct(
            severity="INSUFFICIENT_DATA",
            summary="Output validation failed — safe fallback response.",
            source_citations=[Citation.model_construct(
                id="FALLBACK:validation-failed",
                description="Output validation failed — safe fallback.",
            )],
            region=region,
            timestamp=datetime.utcnow(),
        )
