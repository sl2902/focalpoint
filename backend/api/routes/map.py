"""Map markers endpoint.

GET /map/markers — returns geolocated incident markers for the mobile map view.

Only GDELT Cloud events with valid lat/lon coordinates are included —
events without geo data cannot be placed on the map. No Gemma 4 call is made;
this endpoint is intentionally lightweight so the map renders quickly.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from backend.api.dependencies import get_gdelt_cloud_connector
from backend.api.schemas import MapMarker, MarkersResponse
from backend.ingestion.gdeltcloud_connector import GdeltCloudConnector
from backend.security.rate_limiter import MAP_RATE_LIMIT, limiter

router = APIRouter(prefix="/map", tags=["map"])


@router.get("/markers", response_model=MarkersResponse)
@limiter.limit(MAP_RATE_LIMIT)
async def get_map_markers(
    request: Request,
    region: str = Query(min_length=2, max_length=100),
    days: int = Query(default=7, ge=1, le=30),
    gdelt_cloud: GdeltCloudConnector = Depends(get_gdelt_cloud_connector),
) -> MarkersResponse:
    """Return geolocated incident markers for the mobile map view."""
    events = await gdelt_cloud.fetch_events(region, days=days)

    markers = [
        MapMarker(
            event_id=e.id,
            latitude=e.geo.latitude,
            longitude=e.geo.longitude,
            event_type=e.event_type,
            region=region,
            timestamp=e.event_date,
        )
        for e in events
        if e.geo and e.geo.latitude is not None and e.geo.longitude is not None
    ]

    return MarkersResponse(markers=markers, region=region, total=len(markers))
