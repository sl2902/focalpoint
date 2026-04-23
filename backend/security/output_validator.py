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

# Matches ACLED event IDs: 3 uppercase letters followed by 4+ digits.
# Examples: SYR20240101, PSE20240415, SDN20231207
_ACLED_ID_RE = re.compile(r"^[A-Z]{3}\d{4,}$")


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


class AlertOutput(BaseModel):
    severity: Literal["GREEN", "AMBER", "RED", "CRITICAL", "INSUFFICIENT_DATA"]
    summary: str = Field(min_length=10, max_length=1000)
    source_citations: list[str] = Field(min_length=1)
    region: str
    timestamp: datetime

    @field_validator("source_citations", mode="after")
    @classmethod
    def citations_must_be_real(cls, v: list[str]) -> list[str]:
        """Each citation must be an ACLED event ID or a URL.

        Rejects free-form text masquerading as a citation — prevents
        Gemma 4 from hallucinating plausible-sounding but invalid sources.
        """
        for citation in v:
            if not (_URL_RE.match(citation) or _ACLED_ID_RE.match(citation)):
                raise ValueError(
                    f"Citation must be a URL or ACLED event ID, got: {citation!r}"
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
    try:
        return AlertOutput.model_validate(raw)
    except ValidationError:
        logger.warning(
            "output_validator: AlertOutput failed validation — returning fallback"
        )
        return AlertOutput.model_construct(
            severity="INSUFFICIENT_DATA",
            summary="Output validation failed — safe fallback response.",
            source_citations=["FALLBACK:validation-failed"],
            region=region,
            timestamp=datetime.utcnow(),
        )
