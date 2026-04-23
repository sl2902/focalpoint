"""
Rate limiting — Layer 5 of the FocalPoint security stack.

slowapi Limiter keyed on the device_id request header (set by Expo on first
launch and persisted in SecureStore). Falls back to "unknown-device" when
the header is absent so requests are never silently dropped.

Usage at route level:
    @router.post("/query")
    @limiter.limit(QUERY_RATE_LIMIT)
    async def query(request: Request, ...): ...

Rate limit exceeded → HTTP 429 with Retry-After header (handled by slowapi).
"""

from __future__ import annotations

from slowapi import Limiter
from starlette.requests import Request

QUERY_RATE_LIMIT = "10/minute"
ALERTS_RATE_LIMIT = "30/minute"
MAP_RATE_LIMIT = "30/minute"


def _get_device_id(request: Request) -> str:
    """Return the device_id header, or 'unknown-device' if absent."""
    return request.headers.get("device_id", "unknown-device")


limiter = Limiter(key_func=_get_device_id)
