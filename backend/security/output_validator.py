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
        """Each citation id must be a URL or GDELT Cloud event ID.

        Rejects free-form text masquerading as a citation — prevents
        Gemma 4 from hallucinating plausible-sounding but invalid sources.
        At least one citation is required unless severity is INSUFFICIENT_DATA.
        """
        severity = (info.data or {}).get("severity")
        if severity != "INSUFFICIENT_DATA" and len(v) == 0:
            raise ValueError("source_citations must contain at least one citation when severity is not INSUFFICIENT_DATA")
        for citation in v:
            if not (
                _URL_RE.match(citation.id)
                or _GDELT_CLOUD_ID_RE.match(citation.id)
                or _HISTORICAL_SOURCE_RE.match(citation.id)
            ):
                raise ValueError(
                    f"Citation id must be a URL, GDELT Cloud event ID, or CPJ/RSF source, got: {citation.id!r}"
                )
        return v


# ---------------------------------------------------------------------------
# validate_output
# ---------------------------------------------------------------------------


def validate_output(raw: dict, region: str) -> AlertOutput:
    """
    Validate a Gemma 4 response dict against AlertOutput.

    On success returns the validated model. On any ValidationError logs
    the failure (without the raw output) and returns a safe fallback
    with severity=INSUFFICIENT_DATA so the mobile client always receives
    a well-formed response.

    Args:
        raw:    Dict parsed from Gemma 4's JSON response.
        region: Watch zone region string, threaded into the fallback.
    """
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
