"""Process-wide startup readiness flags for gating upload endpoints."""

from __future__ import annotations

import threading
import time

_lock = threading.Lock()

_api_ready: bool = False
_startup_check_complete: bool = False
_startup_check_ok: bool = False
_initialization_complete: bool = False
_ready_since: float | None = None


def mark_api_ready() -> None:
    global _api_ready, _ready_since
    with _lock:
        _api_ready = True
        _ready_since = time.time()


def mark_startup_check_complete(*, ok: bool) -> None:
    global _startup_check_complete, _startup_check_ok
    with _lock:
        _startup_check_complete = True
        _startup_check_ok = ok


def mark_initialization_complete() -> None:
    global _initialization_complete
    with _lock:
        _initialization_complete = True


def is_upload_allowed() -> bool:
    """Upload endpoints require startup checks to have completed successfully."""
    with _lock:
        return _api_ready and _startup_check_complete and _startup_check_ok


def readiness_snapshot() -> dict:
    with _lock:
        return {
            "api_ready": _api_ready,
            "startup_check_complete": _startup_check_complete,
            "startup_check_ok": _startup_check_ok,
            "initialization_complete": _initialization_complete,
            "upload_allowed": is_upload_allowed(),
            "ready_since": _ready_since,
        }
