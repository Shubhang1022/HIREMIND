"""
ModelService — process-wide embedding model singleton.

Design goals
------------
* Load once at FastAPI startup (or lazily on first use if startup load fails).
* Never download the model inside a background indexing thread.
* Thread-safe: concurrent callers block until the model is ready.
* 60-second load timeout: raises ModelLoadTimeout instead of hanging forever.
* Cache-aware: logs MODEL_CACHE_HIT / MODEL_CACHE_MISS.
* Configurable model name via EMBEDDING_MODEL_NAME env var; defaults to
  BAAI/bge-base-en-v1.5 (438 MB, works on Render free tier).
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
# Use the smaller base model by default — 438 MB vs 1.34 GB for large.
# Override via EMBEDDING_MODEL_NAME environment variable.
_DEFAULT_MODEL = "BAAI/bge-base-en-v1.5"


class ModelLoadTimeout(RuntimeError):
    """Raised when the model does not finish loading within the timeout."""


class ModelLoadFailed(RuntimeError):
    """Raised when the model loading fails with an exception."""


# ---------------------------------------------------------------------------
# Internal state — never access directly outside this module
# ---------------------------------------------------------------------------
_lock = threading.Lock()          # guards all state mutations below
_model = None                      # the loaded SentenceTransformer instance
_model_name: Optional[str] = None # name of the currently loaded model
_load_state: str = "unloaded"     # "unloaded" | "loading" | "loaded" | "failed"
_load_error: Optional[Exception] = None
_load_event = threading.Event()   # set when load finishes (success or failure)
_cache_verdict_logged: bool = False  # ensures CACHE_HIT / CACHE_MISS logged only once


def _get_model_name() -> str:
    """Return the configured model name. Checked every call so env-var changes
    between restarts take effect without code changes."""
    # Priority: EMBEDDING_MODEL_NAME > EMBEDDING_MODEL (legacy) > default
    name = (
        os.environ.get("EMBEDDING_MODEL_NAME")
        or os.environ.get("EMBEDDING_MODEL")
        or _DEFAULT_MODEL
    )
    # Also try settings if available
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
        mem = proc.memory_info()
        vms = mem.vms / (1024 * 1024)
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
    """Ensure HuggingFace cache directories are configured.
    On Render, /app/.cache is ephemeral unless a persistent disk is mounted.
    Log the effective path so operators can verify.
    """
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
    """Run in a daemon thread. Loads the model and updates global state."""
    global _model, _model_name, _load_state, _load_error, _cache_verdict_logged

    _log_memory("PRE_MODEL_LOAD")
    logger.info("[MODEL_SERVICE] [MODEL_LOAD_START] name=%s timeout=%ds", model_name, MODEL_LOAD_TIMEOUT_SECONDS)

    # Check if already cached in EmbeddingEncoder's module-level cache
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
                logger.info("[MODEL_SERVICE] [MODEL_CACHE_HIT] name=%s — model already in _MODEL_CACHE, no download", model_name)
                _cache_verdict_logged = True
            _model = _MODEL_CACHE[model_name]
            _model_name = model_name
            _load_state = "loaded"
            _load_event.set()
            return
        else:
            if not _cache_verdict_logged:
                logger.info("[MODEL_SERVICE] [MODEL_CACHE_MISS] name=%s — downloading/loading from HuggingFace", model_name)
                _cache_verdict_logged = True
    except Exception:
        if not _cache_verdict_logged:
            logger.info("[MODEL_SERVICE] [MODEL_CACHE_MISS] name=%s — _MODEL_CACHE unavailable, loading fresh", model_name)
            _cache_verdict_logged = True

    try:
        _set_hf_cache()

        # Suppress noisy HF warnings when running unauthenticated
        logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
        logging.getLogger("transformers").setLevel(logging.ERROR)

        from sentence_transformers import SentenceTransformer

        # Try loading; fall back through kwargs variations for ST compatibility
        loaded = None
        token = os.environ.get("HF_TOKEN")
        try:
            from app.core.config import settings
            token = settings.hf_token or token
        except Exception:
            pass

        t0 = time.time()
        try:
            if token:
                loaded = SentenceTransformer(model_name, device="cpu", token=token)
            else:
                loaded = SentenceTransformer(model_name, device="cpu")
        except TypeError:
            loaded = SentenceTransformer(model_name, device="cpu")

        elapsed = time.time() - t0
        logger.info(
            "[MODEL_SERVICE] [MODEL_LOAD_COMPLETE] name=%s elapsed=%.1fs",
            model_name, elapsed,
        )

        # Persist in EmbeddingEncoder cache so existing code paths get cache-hit
        try:
            from src.features.embedding import _MODEL_CACHE
            _MODEL_CACHE[model_name] = loaded
        except Exception:
            pass

        _model = loaded
        _model_name = model_name
        _load_state = "loaded"
        _log_memory("POST_MODEL_LOAD")
        logger.info("[MODEL_SERVICE] [MODEL_SINGLETON_CREATED] name=%s — SentenceTransformer instantiated exactly once in this process", model_name)

    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        logger.error(
            "[MODEL_SERVICE] [MODEL_LOAD_FAILED] name=%s error=%s\n%s",
            model_name, exc, tb,
        )
        _load_error = exc
        _load_state = "failed"
    finally:
        _load_event.set()


def preload(model_name: Optional[str] = None) -> bool:
    """
    Kick off async model load in a daemon thread.

    Safe to call from FastAPI lifespan (async context). Returns immediately;
    the model loads in the background. Use ``get_model()`` to retrieve it.

    Returns True if a new load was started, False if already loaded/loading.
    """
    global _load_state, _load_event

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
    Return the loaded SentenceTransformer. Blocks until it is ready.

    If the model has not been preloaded yet, starts loading it now.

    Raises
    ------
    ModelLoadTimeout   if loading does not complete within ``timeout`` seconds.
    ModelLoadFailed    if loading raised an exception.
    """
    global _load_state

    target = _get_model_name()

    with _lock:
        if _load_state == "loaded" and _model is not None:
            # Fast path — already loaded; log reuse (debug level to avoid log spam)
            logger.debug("[MODEL_SERVICE] [MODEL_REUSED] name=%s", _model_name)
            return _model
        if _load_state == "failed":
            raise ModelLoadFailed(
                f"Model '{target}' failed to load: {_load_error}"
            ) from _load_error
        if _load_state == "unloaded":
            # Trigger load now (lazy fallback)
            _load_state = "loading"
            _load_event.clear()
            t = threading.Thread(
                target=_do_load, args=(target,), name="model-lazy-load", daemon=True
            )
            t.start()

    # Wait for the load thread with periodic heartbeat logging
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
        rss = _memory_mb()
        logger.info(
            "[MODEL_SERVICE] [MODEL_LOAD_HEARTBEAT] waited=%.0fs/%.0fs model=%s ram=%.1fMB",
            waited, timeout, target, rss,
        )

    if not _load_event.is_set():
        # Timeout — mark as failed so subsequent calls fail fast
        with _lock:
            global _load_error
            _load_error = ModelLoadTimeout(
                f"Model '{target}' did not finish loading within {timeout}s. "
                "Check HuggingFace Hub connectivity and available RAM."
            )
            _load_state = "failed"
        logger.error(
            "[MODEL_SERVICE] [MODEL_LOAD_TIMEOUT] model=%s timeout=%ds",
            target, int(timeout),
        )
        raise _load_error  # type: ignore[misc]

    if _load_state == "failed":
        raise ModelLoadFailed(
            f"Model '{target}' failed to load: {_load_error}"
        ) from _load_error

    if _model is None:
        raise ModelLoadFailed(f"Model '{target}' load completed but instance is None")

    return _model


def is_loaded() -> bool:
    """Return True if the model is currently loaded and ready."""
    return _load_state == "loaded" and _model is not None


def get_model_name() -> Optional[str]:
    """Return the name of the currently loaded model, or None."""
    return _model_name


def reset() -> None:
    """
    Unload the model and reset state.

    Only call this in tests or if you deliberately want to force a reload.
    Do NOT call during normal request handling.
    """
    global _model, _model_name, _load_state, _load_error
    with _lock:
        _model = None
        _model_name = None
        _load_state = "unloaded"
        _load_error = None
        _load_event.clear()
    try:
        from src.features.embedding import _MODEL_CACHE
        _MODEL_CACHE.clear()
    except Exception:
        pass
    gc.collect()
    logger.info("[MODEL_SERVICE] Model unloaded and cache cleared.")
