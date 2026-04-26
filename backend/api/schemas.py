"""API response schemas for FocalPoint endpoints.

These are the shapes returned to the mobile client — distinct from the
internal AlertOutput and SeverityResult models used between layers.
score and reasoning are intentionally excluded: severity level is what
journalists need; the numeric score and scorer reasoning are internal.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from backend.security.output_validator import Citation


class AlertResponse(BaseModel):
    severity: str
    summary: str
    source_citations: list[Citation]
    region: str
    timestamp: datetime
    confidence: float
    days: int | None = None  # populated by /alerts/feed; None on per-region responses


class MapMarker(BaseModel):
    event_id: str
    latitude: float
    longitude: float
    event_type: str | None
    region: str
    timestamp: str


class MarkersResponse(BaseModel):
    markers: list[MapMarker]
    region: str
    total: int


class QueryResponse(BaseModel):
    answer: str
    severity: str
    source_citations: list[Citation]
    region: str
    timestamp: datetime
    was_sanitised: bool


class TranscribeResponse(BaseModel):
    text: str
    language: str


class HealthResponse(BaseModel):
    status: str
    version: str
