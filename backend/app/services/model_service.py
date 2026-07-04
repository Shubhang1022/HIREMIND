"""
ModelService — process-wide embedding model singleton.

Design goals
------------
* Load once at FastAPI startup (or lazily on first use if startup load fails).
* Never download the model inside a background indexing thread.
* Thread-safe: concurrent callers block until the model is ready.
* Configurable timeout: raises ModelLoadTimeout instead of hanging forever.
* Cache-aware: logs MODEL_CACHE_HIT / MODEL_CACHE_MISS exactly once per process.
* Configurable model name via EMBEDDING_MODEL_NAME env var.
* Memory diagnostics logged before and after load.
"""

from __future__ import annotations

import gc
import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
MODEL_LOAD_TIMEOUT_SECONDS = int(os.environ.get("MODEL_LOAD_TIMEOUT", "120"))
_DEFAULT_MODEL = "BAAI/bge-base-en-v1.5"


class ModelLoadTimeout(RuntimeError):
    """Raised when the model does not finish loading within the timeout."""


class ModelLoadFailed(RuntimeError):
    """Raised when the model loading fails with an exception."""


# ---------------------------------------------------------------------------
# Module-level state — only mutated inside functions that declare them global
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_load_event = threading.Event()

_model = None                        # SentenceTransformer instance once loaded
_model_name: Optional[str] = None   # name of the currently loaded model
_load_state: str = "unloaded"       # "unloaded" | "loading" | "loaded" | "failed"
_load_error: Optional[Exception] = None
_cache_verdict_logged: bool = False  # CACHE_HIT / CACHE_MISS logged exactly once


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _get_model_name() -> str:
    """Resolve model name: EMBEDDING_MODEL_NAME > EMBEDDING_MODEL > settings > default."""
    name = (
        os.environ.get("EMBEDDING_MODEL_NAME")
        or os.environ.get("EMBEDDING_MODEL")
        or _DEFAULT_MODEL
    )
    try:
        from app.core.config import settings
        name = settings.embedding_model or name
    except Exception:
        pass
    return name


def _memory_mb() -> float:
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        return 0.0


def _log_memory(label: str) -> float:
    rss = _memory_mb()
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        vms = proc.memory_info().vms / (1024 * 1024)
        avail = psutil.virtual_memory().available / (1024 * 1024)
        cpu = proc.cpu_percent(interval=None)
        logger.info(
            "[MEMORY_DIAGNOSTICS] %s | RSS=%.1fMB VMS=%.1fMB AvailRAM=%.1fMB CPU=%.1f%%",
            label, rss, vms, avail, cpu,
        )
    except Exception:
        logger.info("[MEMORY_DIAGNOSTICS] %s | RSS=%.1fMB", label, rss)
    return rss


def _set_hf_cache() -> None:
    if not os.environ.get("HF_HOME"):
        os.environ["HF_HOME"] = "/app/.cache/huggingface"
    if not os.environ.get("TRANSFORMERS_CACHE"):
        os.environ["TRANSFORMERS_CACHE"] = "/app/.cache/huggingface"
    if not os.environ.get("SENTENCE_TRANSFORMERS_HOME"):
        os.environ["SENTENCE_TRANSFORMERS_HOME"] = "/app/.cache/sentence-transformers"
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    logger.info(
        "[MODEL_SERVICE] HF cache → HF_HOME=%s SENTENCE_TRANSFORMERS_HOME=%s",
        os.environ["HF_HOME"],
        os.environ["SENTENCE_TRANSFORMERS_HOME"],
    )


def _do_load(model_name: str) -> None:
    """
    Run in a daemon thread.  Loads the model and updates module-level state.

    ALL global declarations are at the very top of this function — before any
    read or write of those names — to satisfy Python's scoping rules.
    """
    # ── ALL globals declared first ─────────────────────────────────────────
    global _model
    global _model_name
    global _load_state
    global _load_error
    global _cache_verdict_logged
    # ───────────────────────────────────────────────────────────────────────

    _log_memory("PRE_MODEL_LOAD")
    logger.info(
        "[MODEL_SERVICE] [MODEL_LOAD_START] name=%s timeout=%ds",
        model_name, MODEL_LOAD_TIMEOUT_SECONDS,
    )

    # ── Check module-level EmbeddingEncoder cache first ───────────────────
    try:
        import sys
        _project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )
        if _project_root not in sys.path:
            sys.path.insert(0, _project_root)
        from src.features.embedding import _MODEL_CACHE
        if model_name in _MODEL_CACHE:
            if not _cache_verdict_logged:
                logger.info(
                    "[MODEL_SERVICE] [MODEL_CACHE_HIT] name=%s — already in _MODEL_CACHE, no download",
                    model_name,
                )
                _cache_verdict_logged = True
            _model = _MODEL_CACHE[model_name]
            _model_name = model_name
            _load_state = "loaded"
            _load_event.set()
            return
        else:
            if not _cache_verdict_logged:
                logger.info(
                    "[MODEL_SERVICE] [MODEL_CACHE_MISS] name=%s — downloading/loading from HuggingFace",
                    model_name,
                )
                _cache_verdict_logged = True
    except Exception:
        if not _cache_verdict_logged:
            logger.info(
                "[MODEL_SERVICE] [MODEL_CACHE_MISS] name=%s — _MODEL_CACHE unavailable, loading fresh",
                model_name,
            )
            _cache_verdict_logged = True

    # ── Load the model ─────────────────────────────────────────────────────
    try:
        _set_hf_cache()
        logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
        logging.getLogger("transformers").setLevel(logging.ERROR)

        from sentence_transformers import SentenceTransformer

        token = os.environ.get("HF_TOKEN")
        try:
            from app.core.config import settings
            token = settings.hf_token or token
        except Exception:
            pass

        # ── Log pre-load diagnostics (requirement 2) ──────────────────────
        hf_home = os.environ.get("HF_HOME", "/app/.cache/huggingface")
        cache_dir_exists = os.path.isdir(hf_home)
        logger.info(
            "[MODEL_SERVICE] [MODEL_LOAD_DIAGNOSTICS] "
            "model=%s cache_dir=%s already_cached=%s download_required=%s ram=%.1fMB",
            model_name, hf_home, cache_dir_exists,
            not cache_dir_exists, _memory_mb(),
        )

        # ── Load with periodic "still loading" heartbeats every 30s ──────
        t0 = time.time()
        _load_done = threading.Event()

        def _heartbeat_logger():
            interval = 30.0
            while not _load_done.wait(timeout=interval):
                elapsed_so_far = time.time() - t0
                logger.warning(
                    "[MODEL_SERVICE] [MODEL_STILL_LOADING] "
                    "elapsed=%.0fs model=%s ram=%.1fMB",
                    elapsed_so_far, model_name, _memory_mb(),
                )
                print(
                    f"[MODEL_SERVICE] [MODEL_STILL_LOADING] "
                    f"elapsed={elapsed_so_far:.0f}s model={model_name} ram={_memory_mb():.1f}MB",
                    flush=True,
                )

        _hb_thread = threading.Thread(
            target=_heartbeat_logger,
            name="model-load-heartbeat",
            daemon=True,
        )
        _hb_thread.start()

        try:
            if token:
                loaded = SentenceTransformer(model_name, device="cpu", token=token)
            else:
                loaded = SentenceTransformer(model_name, device="cpu")
        except TypeError:
            loaded = SentenceTransformer(model_name, device="cpu")
        finally:
            _load_done.set()   # stop heartbeat thread regardless of outcome

        elapsed = time.time() - t0
        dim = loaded.get_sentence_embedding_dimension()
        logger.info(
            "[MODEL_SERVICE] [MODEL_LOAD_COMPLETE] name=%s elapsed=%.1fs "
            "embedding_dim=%s ram=%.1fMB",
            model_name, elapsed, dim, _memory_mb(),
        )
        print(
            f"[MODEL_SERVICE] [MODEL_LOAD_COMPLETE] name={model_name} "
            f"elapsed={elapsed:.1f}s embedding_dim={dim} ram={_memory_mb():.1f}MB",
            flush=True,
        )

        # Back-fill EmbeddingEncoder cache for legacy cache-hit paths
        try:
            from src.features.embedding import _MODEL_CACHE as _MC
            _MC[model_name] = loaded
        except Exception:
            pass

        _model = loaded
        _model_name = model_name
        _load_state = "loaded"
        _log_memory("POST_MODEL_LOAD")
        logger.info(
            "[MODEL_SERVICE] [MODEL_SINGLETON_CREATED] name=%s"
            " — SentenceTransformer instantiated exactly once in this process",
            model_name,
        )

    except Exception as exc:
        import traceback as _tb
        tb = _tb.format_exc()
        logger.error(
            "[MODEL_SERVICE] [MODEL_LOAD_FAILED] name=%s error=%s\n%s",
            model_name, exc, tb,
        )
        _load_error = exc
        _load_state = "failed"

    finally:
        _load_event.set()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def preload(model_name: Optional[str] = None) -> bool:
    """
    Kick off model load in a daemon thread.  Returns immediately.

    Returns True if a new load was started, False if already loaded/loading.
    ALL global declarations are at the very top of this function.
    """
    # ── ALL globals declared first ─────────────────────────────────────────
    global _load_state
    # ───────────────────────────────────────────────────────────────────────

    target = model_name or _get_model_name()

    with _lock:
        if _load_state in ("loaded", "loading"):
            logger.info(
                "[MODEL_SERVICE] preload() called but state=%s — skipping duplicate",
                _load_state,
            )
            return False
        _load_state = "loading"
        _load_event.clear()

    logger.info("[MODEL_SERVICE] Starting background preload for model=%s", target)
    t = threading.Thread(target=_do_load, args=(target,), name="model-preload", daemon=True)
    t.start()
    return True


def get_model(timeout: float = MODEL_LOAD_TIMEOUT_SECONDS):
    """
    Return the loaded SentenceTransformer.  Blocks until ready (up to timeout).

    Raises ModelLoadTimeout if loading does not complete within timeout seconds.
    Raises ModelLoadFailed if loading raised an exception.

    ALL global declarations are at the very top of this function.
    """
    # ── ALL globals declared first ─────────────────────────────────────────
    global _load_state
    global _load_error
    # ───────────────────────────────────────────────────────────────────────

    target = _get_model_name()

    # Fast path — already loaded
    with _lock:
        if _load_state == "loaded" and _model is not None:
            logger.debug("[MODEL_SERVICE] [MODEL_REUSED] name=%s", _model_name)
            return _model

        if _load_state == "failed":
            raise ModelLoadFailed(
                f"Model '{target}' failed to load: {_load_error}"
            ) from _load_error

        if _load_state == "unloaded":
            # Lazy fallback: trigger load now
            _load_state = "loading"
            _load_event.clear()
            t = threading.Thread(
                target=_do_load, args=(target,), name="model-lazy-load", daemon=True
            )
            t.start()

    # Wait with periodic heartbeat logs
    heartbeat_interval = 5.0
    waited = 0.0
    logger.info(
        "[MODEL_SERVICE] Waiting for model load (timeout=%ds, model=%s) …",
        int(timeout), target,
    )
    while waited < timeout:
        chunk = min(heartbeat_interval, timeout - waited)
        if _load_event.wait(timeout=chunk):
            break
        waited += chunk
        logger.info(
            "[MODEL_SERVICE] [MODEL_LOAD_HEARTBEAT] waited=%.0fs/%.0fs model=%s ram=%.1fMB",
            waited, timeout, target, _memory_mb(),
        )

    if not _load_event.is_set():
        # Timed out — record failure so subsequent callers fail fast
        timeout_exc = ModelLoadTimeout(
            f"Model '{target}' did not finish loading within {timeout}s. "
            "Check HuggingFace Hub connectivity and available RAM."
        )
        with _lock:
            _load_error = timeout_exc
            _load_state = "failed"
        logger.error(
            "[MODEL_SERVICE] [MODEL_LOAD_TIMEOUT] model=%s timeout=%ds",
            target, int(timeout),
        )
        raise timeout_exc

    # Re-check state after event was set
    with _lock:
        current_state = _load_state
        current_error = _load_error
        current_model = _model

    if current_state == "failed":
        raise ModelLoadFailed(
            f"Model '{target}' failed to load: {current_error}"
        ) from current_error

    if current_model is None:
        raise ModelLoadFailed(f"Model '{target}' load completed but instance is None")

    return current_model


def is_loaded() -> bool:
    """Return True if the model is currently loaded and ready."""
    return _load_state == "loaded" and _model is not None


def get_model_name() -> Optional[str]:
    """Return the name of the currently loaded model, or None."""
    return _model_name


def get_load_state() -> str:
    """Return the current load state string."""
    return _load_state


def get_load_error() -> Optional[Exception]:
    """Return the load exception if state is 'failed', else None."""
    return _load_error


def reset() -> None:
    """
    Unload the model and reset all state.

    Only for tests.  Never call during normal request handling.
    ALL global declarations are at the very top of this function.
    """
    # ── ALL globals declared first ─────────────────────────────────────────
    global _model
    global _model_name
    global _load_state
    global _load_error
    global _cache_verdict_logged
    # ───────────────────────────────────────────────────────────────────────

    with _lock:
        _model = None
        _model_name = None
        _load_state = "unloaded"
        _load_error = None
        _cache_verdict_logged = False
        _load_event.clear()

    try:
        from src.features.embedding import _MODEL_CACHE
        _MODEL_CACHE.clear()
    except Exception:
        pass

    gc.collect()
    logger.info("[MODEL_SERVICE] Model unloaded and cache cleared.")
