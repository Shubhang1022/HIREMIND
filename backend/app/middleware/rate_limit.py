"""Simple in-memory rate limiter for API endpoints."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

from fastapi import HTTPException, Request, status


@dataclass
class RateLimitStore:
    requests: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    limit: int = 100
    window_seconds: int = 60


_store = RateLimitStore()


async def rate_limit_middleware(request: Request, call_next):
    """Rate limit by IP address. Always pass OPTIONS (CORS preflight) through."""
    # Always let CORS preflight and health checks through
    if request.method == "OPTIONS" or request.url.path in (
        "/health", "/api/v1/health", "/docs", "/redoc", "/openapi.json"
    ):
        return await call_next(request)

    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    window_start = now - _store.window_seconds

    _store.requests[client_ip] = [t for t in _store.requests[client_ip] if t > window_start]

    if len(_store.requests[client_ip]) >= _store.limit:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": "Rate limit exceeded. Try again later."},
        )

    _store.requests[client_ip].append(now)
    response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = str(_store.limit)
    response.headers["X-RateLimit-Remaining"] = str(
        _store.limit - len(_store.requests[client_ip])
    )
    return response
