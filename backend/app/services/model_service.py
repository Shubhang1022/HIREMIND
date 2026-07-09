"""
ModelService — process-wide embedding model singleton.

Design goals (updated for Railway OOM fix)
-------------------------------------------
* LAZY loading: model is NOT loaded at startup.
  The first call to get_model() triggers loading.
* Exactly one SentenceTransformer instance per process — enforced by _lock.
* Thread-safe: concurrent callers all block on the same _load_event.
* Configurable timeout via MODEL_LOAD_TIMEOUT env var (default 120 s).
* Full memory instrumentation: RSS logged before/after every expensive step.
* Torch diagnostics: version, CUDA build check, thread limits applied before import.
* GC forced immediately after model construction to release init-time temporaries.
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
_DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


class ModelLoadTimeout(RuntimeError):
    """Raised when the model does not finish loading within the timeout."""


class ModelLoadFailed(RuntimeError):
    """Raised when the model loading fails with an exception."""


# ---------------------------------------------------------------------------
# Module-level state — mutated only inside functions that declare them global
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_load_event = threading.Event()

_model = None                         # SentenceTransformer instance once loaded
_model_name: Optional[str] = None    # name of the currently loaded model
_load_state: str = "unloaded"        # "unloaded" | "loading" | "loaded" | "failed"
_load_error: Optional[Exception] = None
_cache_verdict_logged: bool = False   # CACHE_HIT / CACHE_MISS logged exactly once


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
    """Log RSS / VMS / available RAM and return RSS in MB."""
    rss = _memory_mb()
    try:
        import psutil
        proc  = psutil.Process(os.getpid())
        vms   = proc.memory_info().vms / (1024 * 1024)
        avail = psutil.virtual_memory().available / (1024 * 1024)
        cpu   = proc.cpu_percent(interval=None)
        nth   = proc.num_threads()
        logger.info(
            "[MEMORY_DIAGNOSTICS] %s | RSS=%.1fMB VMS=%.1fMB AvailRAM=%.1fMB "
            "CPU=%.1f%% Threads=%d",
            label, rss, vms, avail, cpu, nth,
        )
        print(
            f"[MEMORY_DIAGNOSTICS] {label} | RSS={rss:.1f}MB VMS={vms:.1f}MB "
            f"AvailRAM={avail:.1f}MB CPU={cpu:.1f}% Threads={nth}",
            flush=True,
        )
    except Exception:
        logger.info("[MEMORY_DIAGNOSTICS] %s | RSS=%.1fMB", label, rss)
        print(f"[MEMORY_DIAGNOSTICS] {label} | RSS={rss:.1f}MB", flush=True)
    return rss


def _set_env_limits() -> None:
    """
    Phase 5 — set thread-count environment variables BEFORE any ML library
    import to prevent torch / OpenBLAS / MKL from spawning large thread pools.

    Must be called before 'import torch' or 'from sentence_transformers import …'.
    """
    for var in (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ.setdefault(var, "1")

    # Prevent HuggingFace tokenizers from forking child processes
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    logger.info(
        "[MODEL_SERVICE] Thread limits set: OMP=%s MKL=%s OPENBLAS=%s NUMEXPR=%s TOKENIZERS_PARALLELISM=%s",
        os.environ["OMP_NUM_THREADS"],
        os.environ["MKL_NUM_THREADS"],
        os.environ["OPENBLAS_NUM_THREADS"],
        os.environ["NUMEXPR_NUM_THREADS"],
        os.environ["TOKENIZERS_PARALLELISM"],
    )


def _set_hf_cache() -> None:
    """Configure HuggingFace / sentence-transformers cache directories."""
    os.environ.setdefault("HF_HOME", "/app/.cache/huggingface")
    os.environ.setdefault("TRANSFORMERS_CACHE", "/app/.cache/huggingface")
    os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", "/app/.cache/sentence-transformers")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    logger.info(
        "[MODEL_SERVICE] HF cache → HF_HOME=%s SENTENCE_TRANSFORMERS_HOME=%s",
        os.environ["HF_HOME"],
        os.environ["SENTENCE_TRANSFORMERS_HOME"],
    )


def _log_torch_diagnostics() -> None:
    """
    Phase 4 — log torch version, CUDA build status, and thread config.
    Called inside the load thread immediately after 'import torch'.
    """
    try:
        import torch  # already imported by this point inside _do_load
        ver   = torch.__version__
        cuda  = getattr(torch.version, "cuda", None)
        avail = torch.cuda.is_available() if hasattr(torch, "cuda") else False
        nthrd = torch.get_num_threads()

        is_cuda_build = cuda is not None and cuda != "None"

        print(
            f"\n[TORCH_DIAGNOSTICS] ================================\n"
            f"  torch.__version__         : {ver}\n"
            f"  torch.version.cuda        : {cuda}\n"
            f"  torch.cuda.is_available() : {avail}\n"
            f"  torch.get_num_threads()   : {nthrd}\n"
            f"  OMP_NUM_THREADS           : {os.environ.get('OMP_NUM_THREADS', 'NOT SET')}\n"
            f"  MKL_NUM_THREADS           : {os.environ.get('MKL_NUM_THREADS', 'NOT SET')}\n"
            f"  CUDA build installed      : {is_cuda_build}\n"
            f"  WARNING (if CUDA build)   : {'CUDA wheel wastes ~350 MB RSS on CPU-only host' if is_cuda_build else 'CPU build — optimal'}\n"
            f"[TORCH_DIAGNOSTICS] ================================\n",
            flush=True,
        )
        logger.info(
            "[TORCH_DIAGNOSTICS] version=%s cuda=%s cuda_available=%s "
            "threads=%d omp=%s mkl=%s cuda_build=%s",
            ver, cuda, avail, nthrd,
            os.environ.get("OMP_NUM_THREADS"),
            os.environ.get("MKL_NUM_THREADS"),
            is_cuda_build,
        )
        if is_cuda_build:
            logger.warning(
                "[TORCH_DIAGNOSTICS] CUDA build detected (torch+cu*). "
                "This wastes ~350 MB RSS on a CPU-only Railway container. "
                "Fix: pin torch+cpu in requirements.txt with --extra-index-url "
                "https://download.pytorch.org/whl/cpu"
            )

        # Phase 5 — apply runtime thread limits after torch import
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
        logger.info(
            "[TORCH_DIAGNOSTICS] Applied: set_num_threads(1) set_num_interop_threads(1) "
            "→ new thread count=%d",
            torch.get_num_threads(),
        )
    except Exception as exc:
        logger.warning("[TORCH_DIAGNOSTICS] Could not run torch diagnostics: %s", exc)


def _verify_hf_cache(model_name: str) -> bool:
    """Check whether the Docker pre-downloaded model files are present on disk."""
    st_home = os.environ.get("SENTENCE_TRANSFORMERS_HOME", "/app/.cache/sentence-transformers")
    # sentence-transformers stores models in <ST_HOME>/<org>_<model>/ or <org>/<model>/
    sanitized = model_name.replace("/", "_")
    candidate_dirs = [
        os.path.join(st_home, sanitized),
        os.path.join(st_home, model_name.replace("/", os.sep)),
    ]
    for d in candidate_dirs:
        if os.path.isdir(d) and os.listdir(d):
            logger.info(
                "[HF_CACHE_AUDIT] CACHE_HIT model=%s path=%s files=%s",
                model_name, d, os.listdir(d)[:5],
            )
            print(f"[HF_CACHE_AUDIT] CACHE_HIT model={model_name} path={d}", flush=True)
            return True

    # List what IS available for debugging
    available: list[str] = []
    if os.path.isdir(st_home):
        available = os.listdir(st_home)
    logger.warning(
        "[HF_CACHE_AUDIT] CACHE_MISS model=%s st_home=%s available=%s "
        "— model will be downloaded at runtime (OOM risk on small containers)",
        model_name, st_home, available,
    )
    print(
        f"[HF_CACHE_AUDIT] CACHE_MISS model={model_name} st_home={st_home} "
        f"available={available}",
        flush=True,
    )
    return False


def _do_load(model_name: str) -> None:
    """
    Runs in a daemon thread (or directly via get_model's lazy path).

    Execution order (Phases 3-6):
      1. Set env limits (Phase 5)
      2. Set HF cache paths
      3. Log RSS before any ML import (Phase 3)
      4. import torch → log RSS + torch diagnostics (Phase 4)
      5. import transformers → log RSS (Phase 3)
      6. import sentence_transformers → log RSS (Phase 3)
      7. Verify HF cache on disk (Phase 6 / HFCacheAudit)
      8. SentenceTransformer(...) → log RSS (Phase 3)
      9. gc.collect() → log RSS before/after (Phase 6)
     10. Back-fill _MODEL_CACHE
     11. Set _load_state = "loaded", signal _load_event
    """
    global _model, _model_name, _load_state, _load_error, _cache_verdict_logged

    # ── Phase 5: set thread limits BEFORE any ML import ────────────────────
    _set_env_limits()
    _set_hf_cache()

    # ── Phase 3: baseline RSS before any ML import ─────────────────────────
    _log_memory("PRE_TORCH_IMPORT")

    logger.info(
        "[MODEL_SERVICE] [MODEL_LOAD_START] name=%s timeout=%ds pid=%d",
        model_name, MODEL_LOAD_TIMEOUT_SECONDS, os.getpid(),
    )
    print(
        f"[MODEL_SERVICE] [MODEL_LOAD_START] name={model_name} "
        f"timeout={MODEL_LOAD_TIMEOUT_SECONDS}s pid={os.getpid()}",
        flush=True,
    )

    # ── Check module-level EmbeddingEncoder cache first ─────────────────────
    try:
        import sys as _sys
        _project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )
        if _project_root not in _sys.path:
            _sys.path.insert(0, _project_root)
        from src.features.embedding import _MODEL_CACHE
        if model_name in _MODEL_CACHE:
            if not _cache_verdict_logged:
                logger.info(
                    "[MODEL_SERVICE] [MODEL_CACHE_HIT] name=%s — already in _MODEL_CACHE",
                    model_name,
                )
                _cache_verdict_logged = True
            _model = _MODEL_CACHE[model_name]
            _model_name = model_name
            _load_state = "loaded"
            _load_event.set()
            _log_memory("CACHE_HIT_NO_LOAD")
            return
        else:
            if not _cache_verdict_logged:
                logger.info(
                    "[MODEL_SERVICE] [MODEL_CACHE_MISS] name=%s — will load fresh",
                    model_name,
                )
                _cache_verdict_logged = True
    except Exception:
        if not _cache_verdict_logged:
            logger.info(
                "[MODEL_SERVICE] [MODEL_CACHE_MISS] name=%s — _MODEL_CACHE unavailable",
                model_name,
            )
            _cache_verdict_logged = True

    # ── Silence noisy HF loggers ─────────────────────────────────────────────
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
    logging.getLogger("transformers").setLevel(logging.ERROR)
    logging.getLogger("sentence_transformers").setLevel(logging.WARNING)

    try:
        # ── Phase 3+4: import torch, log diagnostics ─────────────────────
        import torch  # noqa: F401  — may be CUDA build, see diagnostics
        _log_memory("POST_TORCH_IMPORT")
        _log_torch_diagnostics()   # logs version, CUDA flag, applies thread limits

        # ── Phase 3: import transformers ────────────────────────────────
        import transformers  # noqa: F401
        _log_memory("POST_TRANSFORMERS_IMPORT")

        # ── Phase 3: import sentence_transformers ───────────────────────
        from sentence_transformers import SentenceTransformer
        _log_memory("POST_SENTENCE_TRANSFORMERS_IMPORT")

        # ── HF cache verification ────────────────────────────────────────
        _verify_hf_cache(model_name)

        # ── Resolve optional HF token ────────────────────────────────────
        token: Optional[str] = os.environ.get("HF_TOKEN")
        try:
            from app.core.config import settings
            token = settings.hf_token or token
        except Exception:
            pass

        # ── Phase 3: RSS before SentenceTransformer construction ────────
        rss_pre_st = _log_memory("PRE_SENTENCE_TRANSFORMER_CONSTRUCTION")

        t0 = time.time()

        # ── Heartbeat thread: prints every 30 s while model loads ───────
        _load_done_evt = threading.Event()

        def _heartbeat():
            while not _load_done_evt.wait(timeout=30.0):
                logger.warning(
                    "[MODEL_SERVICE] [MODEL_STILL_LOADING] elapsed=%.0fs "
                    "model=%s ram=%.1fMB",
                    time.time() - t0, model_name, _memory_mb(),
                )
                print(
                    f"[MODEL_SERVICE] [MODEL_STILL_LOADING] "
                    f"elapsed={time.time()-t0:.0f}s model={model_name} "
                    f"ram={_memory_mb():.1f}MB",
                    flush=True,
                )

        _hb = threading.Thread(target=_heartbeat, name="model-load-heartbeat", daemon=True)
        _hb.start()

        try:
            if token:
                loaded = SentenceTransformer(model_name, device="cpu", token=token)
            else:
                loaded = SentenceTransformer(model_name, device="cpu")
        except TypeError:
            # Older sentence-transformers version without token kwarg
            loaded = SentenceTransformer(model_name, device="cpu")
        finally:
            _load_done_evt.set()

        elapsed = time.time() - t0
        dim = loaded.get_sentence_embedding_dimension()

        # ── Phase 3: RSS immediately after SentenceTransformer() ────────
        rss_post_st = _log_memory("POST_SENTENCE_TRANSFORMER_CONSTRUCTION")

        logger.info(
            "[MODEL_SERVICE] [MODEL_LOAD_COMPLETE] name=%s elapsed=%.1fs "
            "embedding_dim=%d rss_delta=%.1fMB ram=%.1fMB",
            model_name, elapsed, dim, rss_post_st - rss_pre_st, rss_post_st,
        )
        print(
            f"[MODEL_SERVICE] [MODEL_LOAD_COMPLETE] name={model_name} "
            f"elapsed={elapsed:.1f}s embedding_dim={dim} "
            f"rss_delta={rss_post_st - rss_pre_st:.1f}MB ram={rss_post_st:.1f}MB",
            flush=True,
        )

        # ── Phase 6: gc.collect() immediately after construction ────────
        rss_before_gc = _log_memory("PRE_GC_COLLECT")
        gc.collect()
        # Also try to release any PyTorch CPU allocator cache
        try:
            if hasattr(torch, "cuda") and torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        rss_after_gc = _log_memory("POST_GC_COLLECT")
        logger.info(
            "[MODEL_SERVICE] [GC_RESULT] freed=%.1fMB rss_after=%.1fMB",
            rss_before_gc - rss_after_gc, rss_after_gc,
        )
        print(
            f"[MODEL_SERVICE] [GC_RESULT] freed={rss_before_gc - rss_after_gc:.1f}MB "
            f"rss_after={rss_after_gc:.1f}MB",
            flush=True,
        )

        # ── Back-fill EmbeddingEncoder cache for legacy paths ───────────
        try:
            from src.features.embedding import _MODEL_CACHE as _MC
            _MC[model_name] = loaded
        except Exception:
            pass

        # ── Commit singleton ─────────────────────────────────────────────
        _model = loaded
        _model_name = model_name
        _load_state = "loaded"
        _log_memory("POST_MODEL_LOAD_FINAL")
        logger.info(
            "[MODEL_SERVICE] [MODEL_SINGLETON_CREATED] id=%d name=%s "
            "— SentenceTransformer instantiated exactly once in this process",
            id(loaded), model_name,
        )
        print(
            f"[MODEL_SERVICE] [MODEL_SINGLETON_CREATED] id={id(loaded)} name={model_name}",
            flush=True,
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
    REMOVED FROM STARTUP — kept only as a no-op compatibility shim.

    The model is now loaded lazily on the first get_model() call.
    Calling preload() is safe but does nothing unless state is "unloaded".

    Returns True if a new background load was started, False otherwise.
    """
    global _load_state

    target = model_name or _get_model_name()

    with _lock:
        if _load_state in ("loaded", "loading"):
            logger.info(
                "[MODEL_SERVICE] preload() called but state=%s — skipping (lazy-load mode)",
                _load_state,
            )
            return False
        # Still allow explicit preload calls from tests or manual triggers
        _load_state = "loading"
        _load_event.clear()

    logger.info(
        "[MODEL_SERVICE] preload() called explicitly — starting background load for model=%s",
        target,
    )
    t = threading.Thread(target=_do_load, args=(target,), name="model-preload", daemon=True)
    t.start()
    return True


def get_model(timeout: float = MODEL_LOAD_TIMEOUT_SECONDS):
    """
    Return the loaded SentenceTransformer.

    Lazy-load behaviour (Phase 2):
    - If state is "unloaded": acquire lock, start loading thread, release lock.
    - If state is "loading":  wait on _load_event (up to timeout).
    - If state is "loaded":   return cached instance immediately (fast path).
    - If state is "failed":   raise ModelLoadFailed immediately (fail fast).

    Concurrent callers all wait for the SAME load operation — exactly one
    SentenceTransformer is ever created per process.

    Raises:
        ModelLoadTimeout  — model did not finish within timeout seconds
        ModelLoadFailed   — model loading raised an exception
    """
    global _load_state, _load_error

    target = _get_model_name()

    # ── Fast path: already loaded ─────────────────────────────────────────
    with _lock:
        if _load_state == "loaded" and _model is not None:
            logger.debug("[MODEL_SERVICE] [MODEL_REUSED] id=%d name=%s", id(_model), _model_name)
            return _model

        if _load_state == "failed":
            raise ModelLoadFailed(
                f"Model '{target}' failed to load: {_load_error}"
            ) from _load_error

        if _load_state == "unloaded":
            # Phase 2: first caller triggers the singleton load
            _load_state = "loading"
            _load_event.clear()
            logger.info(
                "[MODEL_SERVICE] [LAZY_LOAD_TRIGGERED] "
                "First request requiring embeddings — starting model load. "
                "model=%s pid=%d",
                target, os.getpid(),
            )
            print(
                f"[MODEL_SERVICE] [LAZY_LOAD_TRIGGERED] "
                f"First embedding request — loading model={target}",
                flush=True,
            )
            t = threading.Thread(
                target=_do_load, args=(target,), name="model-lazy-load", daemon=True
            )
            t.start()
        # else: state == "loading" — another thread is already loading, just wait

    # ── Wait for load to complete (all callers share same event) ─────────
    heartbeat_interval = 5.0
    waited = 0.0
    logger.info(
        "[MODEL_SERVICE] Waiting for lazy model load (timeout=%ds, model=%s) …",
        int(timeout), target,
    )
    while waited < timeout:
        chunk = min(heartbeat_interval, timeout - waited)
        if _load_event.wait(timeout=chunk):
            break
        waited += chunk
        logger.info(
            "[MODEL_SERVICE] [MODEL_LOAD_HEARTBEAT] waited=%.0fs/%.0fs "
            "model=%s ram=%.1fMB",
            waited, timeout, target, _memory_mb(),
        )

    if not _load_event.is_set():
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

    # ── Re-check state after event fired ─────────────────────────────────
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
    """Return the current load state string: 'unloaded'|'loading'|'loaded'|'failed'."""
    return _load_state


def get_load_error() -> Optional[Exception]:
    """Return the load exception if state is 'failed', else None."""
    return _load_error


def reset() -> None:
    """
    Unload the model and reset all state.  For tests only.
    Never call during normal request handling.
    """
    global _model, _model_name, _load_state, _load_error, _cache_verdict_logged

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
    logger.info("[MODEL_SERVICE] Model unloaded and cache cleared (reset called).")
