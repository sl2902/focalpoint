"""API response schemas for FocalPoint endpoints.

These are the shapes returned to the mobile client — distinct from the
internal AlertOutput and SeverityResult models used between layers.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AlertResponse(BaseModel):
    severity: str
    summary: str
    source_citations: list[str]
    region: str
    timestamp: datetime
    score: float
    confidence: float
    reasoning: str


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
    source_citations: list[str]
    region: str
    timestamp: datetime
    was_sanitised: bool


class HealthResponse(BaseModel):
    status: str
    version: str
