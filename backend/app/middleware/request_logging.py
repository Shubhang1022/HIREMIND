"""Request logging middleware — logs every request without modifying CORS."""

from __future__ import annotations

import logging
import time

from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


async def request_logging_middleware(request: Request, call_next) -> Response:
    start = time.perf_counter()
    method = request.method
    path = request.url.path
    content_length = request.headers.get("content-length", "unknown")

    logger.info(
        "[REQUEST]\n%s\n%s\ncontent_length=%s",
        method,
        path,
        content_length,
    )

    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "[REQUEST_COMPLETE]\nstatus=%s\nduration=%.0fms\npath=%s",
            status_code,
            duration_ms,
            path,
        )
        return response
    except Exception as exc:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.exception(
            "[REQUEST_EXCEPTION]\nmethod=%s\npath=%s\nduration=%.0fms\nerror=%s",
            method,
            path,
            duration_ms,
            exc,
        )
        raise
