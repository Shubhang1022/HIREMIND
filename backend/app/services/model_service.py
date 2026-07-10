"""
ModelService — process-wide embedding model singleton.
Instrumented with strict concurrency logging.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Outer caller timeout: how long get_model() will wait for the full load.
MODEL_LOAD_TIMEOUT_SECONDS = int(os.environ.get("MODEL_LOAD_TIMEOUT", "120"))

_DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


class ModelLoadTimeout(RuntimeError):
    """Raised when a model loading stage exceeds the timeout."""


class ModelLoadFailed(RuntimeError):
    """Raised when the model loading fails (missing files, import error, etc.)."""


# ---------------------------------------------------------------------------
# Concurrency Instrumentation Helpers
# ---------------------------------------------------------------------------

class InstrumentedLock:
    def __init__(self, name: str):
        self._real_lock = threading.Lock()
        self._name = name

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        tid = threading.get_ident()
        tname = threading.current_thread().name
        print(f"[LOCK_ACQUIRE] lock={self._name} thread={tname} (tid={tid})", flush=True)
        logger.info(f"[LOCK_ACQUIRE] lock={self._name} thread={tname} (tid={tid})")
        res = self._real_lock.acquire(blocking=blocking, timeout=timeout)
        if res:
            print(f"[LOCK_ACQUIRED] lock={self._name} thread={tname} (tid={tid})", flush=True)
            logger.info(f"[LOCK_ACQUIRED] lock={self._name} thread={tname} (tid={tid})")
        return res

    def release(self) -> None:
        tid = threading.get_ident()
        tname = threading.current_thread().name
        print(f"[LOCK_RELEASE] lock={self._name} thread={tname} (tid={tid})", flush=True)
        logger.info(f"[LOCK_RELEASE] lock={self._name} thread={tname} (tid={tid})")
        self._real_lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


class InstrumentedEvent:
    def __init__(self, name: str):
        self._real_event = threading.Event()
        self._name = name

    def is_set(self) -> bool:
        return self._real_event.is_set()

    def set(self) -> None:
        tid = threading.get_ident()
        tname = threading.current_thread().name
        print(f"[EVENT_SET] event={self._name} thread={tname} (tid={tid})", flush=True)
        logger.info(f"[EVENT_SET] event={self._name} thread={tname} (tid={tid})")
        self._real_event.set()

    def clear(self) -> None:
        tid = threading.get_ident()
        tname = threading.current_thread().name
        print(f"[EVENT_CLEAR] event={self._name} thread={tname} (tid={tid})", flush=True)
        logger.info(f"[EVENT_CLEAR] event={self._name} thread={tname} (tid={tid})")
        self._real_event.clear()

    def wait(self, timeout: Optional[float] = None) -> bool:
        tid = threading.get_ident()
        tname = threading.current_thread().name
        print(f"[EVENT_WAIT_START] event={self._name} timeout={timeout} thread={tname} (tid={tid})", flush=True)
        logger.info(f"[EVENT_WAIT_START] event={self._name} timeout={timeout} thread={tname} (tid={tid})")
        res = self._real_event.wait(timeout=timeout)
        print(f"[EVENT_WAIT_END] event={self._name} result={res} thread={tname} (tid={tid})", flush=True)
        logger.info(f"[EVENT_WAIT_END] event={self._name} result={res} thread={tname} (tid={tid})")
        return res


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_lock = InstrumentedLock("state_lock")
_load_event = InstrumentedEvent("load_event")

_model = None
_model_name: Optional[str] = None
_load_state: str = "unloaded"       # "unloaded"|"loading"|"loaded"|"failed"
_load_error: Optional[Exception] = None
_current_stage: str = "idle"        # tracks which stage is active for timeout logging
_main_loop: Optional[asyncio.AbstractEventLoop] = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _main_loop
    _main_loop = loop


# ---------------------------------------------------------------------------
# Utility helpers
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


def _rss_mb() -> float:
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        return 0.0


def _cpu_pct() -> float:
    try:
        import psutil
        return psutil.Process(os.getpid()).cpu_percent(interval=None)
    except Exception:
        return 0.0


def _thread_count() -> int:
    return threading.active_count()


def _stage(label: str, t0_global: float) -> None:
    """
    Phase 3: print per-stage checkpoint with elapsed, RSS, CPU, threads.
    Also updates _current_stage for timeout logging.
    """
    global _current_stage
    _current_stage = label
    elapsed = time.time() - t0_global
    rss = _rss_mb()
    cpu = _cpu_pct()
    nth = _thread_count()
    msg = (
        f"[STAGE] {label:<36} | "
        f"elapsed={elapsed:6.2f}s | "
        f"RSS={rss:6.1f}MB | "
        f"CPU={cpu:5.1f}% | "
        f"threads={nth}"
    )
    logger.info(msg)
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Phase 4: HF offline env vars — set at import time, before any ML library loads
# ---------------------------------------------------------------------------

def _set_offline_mode() -> None:
    """
    Phase 4 — force HF ecosystem into fully offline mode.
    Must be called before any import of transformers / huggingface_hub.
    """
    os.environ["HF_HUB_OFFLINE"]      = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"]  = "1"
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY",        "1")
    logger.info(
        "[MODEL_SERVICE] Offline mode: HF_HUB_OFFLINE=1 "
        "TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1"
    )
    print("[MODEL_SERVICE] HF offline mode ENABLED - no network requests permitted", flush=True)


def _set_env_limits() -> None:
    """Phase 5 — thread limits before any ML library import."""
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS",
                "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(var, "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    logger.info(
        "[MODEL_SERVICE] Thread limits: OMP=%s MKL=%s OPENBLAS=%s "
        "NUMEXPR=%s TOKENIZERS_PARALLELISM=%s",
        os.environ["OMP_NUM_THREADS"], os.environ["MKL_NUM_THREADS"],
        os.environ["OPENBLAS_NUM_THREADS"], os.environ["NUMEXPR_NUM_THREADS"],
        os.environ["TOKENIZERS_PARALLELISM"],
    )


def _set_hf_cache() -> None:
    """Configure HuggingFace cache dirs (setdefault — don't override Dockerfile ENV)."""
    os.environ.setdefault("HF_HOME", "/app/.cache/huggingface")
    os.environ.setdefault("TRANSFORMERS_CACHE", "/app/.cache/huggingface")
    os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", "/app/.cache/sentence-transformers")


# ---------------------------------------------------------------------------
# Phase 1 + 7: Docker cache verification
# ---------------------------------------------------------------------------

def _find_weight_files(cache_root: Path) -> list[Path]:
    """Recursively find all weight files under cache_root (fallback strategy)."""
    weight_names = {"model.safetensors", "pytorch_model.bin"}
    found: list[Path] = []
    if not cache_root.is_dir():
        return found
    for p in cache_root.rglob("*"):
        if p.is_file() and p.name in weight_names:
            found.append(p)
    return found


def _discover_model_path_from_object(model_obj: object) -> tuple[Optional[Path], str]:
    """
    Phase 1 — try to determine the on-disk model directory directly from the
    loaded SentenceTransformer instance, using only public/documented attributes.

    Returns (resolved_dir, source_attr) or (None, "none") if discovery fails.
    """
    weight_names = {"model.safetensors", "pytorch_model.bin"}

    # ── Probe 1: tokenizer.name_or_path (sentence-transformers ≥ 2.0) ────
    try:
        tok = getattr(model_obj, "tokenizer", None)
        if tok is not None:
            nop = getattr(tok, "name_or_path", None)
            if nop:
                candidate = Path(nop)
                if candidate.is_dir():
                    return candidate, "tokenizer.name_or_path"
    except Exception:
        pass

    # ── Probe 2: first module's auto_model.config._name_or_path ──────────
    try:
        first_module = model_obj[0]  # type: ignore[index]
        auto = getattr(first_module, "auto_model", None)
        if auto is not None:
            cfg = getattr(auto, "config", None)
            if cfg is not None:
                nop = getattr(cfg, "_name_or_path", None)
                if nop:
                    candidate = Path(nop)
                    if candidate.is_dir():
                        return candidate, "modules[0].auto_model.config._name_or_path"
    except Exception:
        pass

    # ── Probe 3: iterate _modules dict ────────────────────────────────────
    try:
        modules = getattr(model_obj, "_modules", {}) or {}
        for _mname, mod in modules.items():
            for sub_attr in ("auto_model", "model"):
                sub = getattr(mod, sub_attr, None)
                if sub is None:
                    continue
                cfg = getattr(sub, "config", None)
                if cfg is None:
                    continue
                nop = getattr(cfg, "_name_or_path", None)
                if nop:
                    candidate = Path(nop)
                    if candidate.is_dir():
                        return candidate, f"_modules[{_mname!r}].{sub_attr}.config._name_or_path"
    except Exception:
        pass

    return None, "none"


def _verify_model_dir(model_dir: Path) -> tuple[bool, list[Path], int]:
    """
    Phase 2 — validate a known model directory.
    Returns (ok, weight_files_found, total_bytes).
    """
    weight_names = {"model.safetensors", "pytorch_model.bin"}
    if not model_dir.is_dir():
        return False, [], 0
    all_files = [p for p in model_dir.rglob("*") if p.is_file()]
    weight_files = [p for p in all_files if p.name in weight_names]
    total_bytes = sum(p.stat().st_size for p in all_files)
    return bool(weight_files), weight_files, total_bytes


def verify_docker_cache(model_name: str) -> tuple[bool, list[str], Path]:
    """
    Phase 1 + 7: Verify the Docker-baked model cache is complete.
    """
    print("[ENTER verify_cache()]", flush=True)
    logger.info("[ENTER verify_cache()]")
    try:
        t_verify_start = time.time()
        cache_root = Path(
            os.environ.get("SENTENCE_TRANSFORMERS_HOME", "/app/.cache/sentence-transformers")
        )
        conventional_dir = cache_root / model_name.replace("/", "_")

        # -- Print env context ----------------------------------------------------
        print(
            f"\n[MODEL_CACHE_VERIFY] -----------------------------------------\n"
            f"  HF_HOME                   : {os.environ.get('HF_HOME')}\n"
            f"  TRANSFORMERS_CACHE        : {os.environ.get('TRANSFORMERS_CACHE')}\n"
            f"  SENTENCE_TRANSFORMERS_HOME: {os.environ.get('SENTENCE_TRANSFORMERS_HOME')}\n"
            f"  model_name                : {model_name}\n"
            f"  cache_root                : {cache_root}\n"
            f"  cache_root_exists         : {cache_root.is_dir()}\n"
            f"  conventional_dir          : {conventional_dir}\n"
            f"  conventional_dir_exists   : {conventional_dir.is_dir()}\n"
            f"[MODEL_CACHE_VERIFY] -----------------------------------------\n",
            flush=True,
        )

        if not cache_root.is_dir():
            missing = [f"cache_root {cache_root} (directory missing)"]
            _log_cache_invalid(model_name, conventional_dir, missing)
            return False, missing, conventional_dir

        t_direct_start = time.time()
        strategy = "direct"
        resolved_dir: Optional[Path] = None
        weight_files: list[Path] = []

        if conventional_dir.is_dir():
            ok_dir, wf, total_bytes = _verify_model_dir(conventional_dir)
            if ok_dir:
                resolved_dir = conventional_dir
                weight_files = wf
        
        t_direct_elapsed = time.time() - t_direct_start

        if resolved_dir is None:
            strategy = "rglob"
            print(
                f"[MODEL_PATH_DISCOVERY_FAILED] conventional_dir={conventional_dir} "
                f"- falling_back_to_recursive_scan=True",
                flush=True,
            )
            t_rglob_start = time.time()
            weight_files = _find_weight_files(cache_root)
            t_rglob_elapsed = time.time() - t_rglob_start

            if weight_files:
                resolved_dir = weight_files[0].parent
                _, _, total_bytes = _verify_model_dir(resolved_dir)
            else:
                total_bytes = 0

            print(
                f"[CACHE_DISCOVERY_PERF] strategy=rglob elapsed={t_rglob_elapsed:.3f}s "
                f"weight_files_found={len(weight_files)}",
                flush=True,
            )
        else:
            print(
                f"[CACHE_DISCOVERY_PERF] strategy=direct elapsed={t_direct_elapsed:.3f}s "
                f"weight_files_found={len(weight_files)}",
                flush=True,
            )

        if not weight_files:
            top_level = sorted(p.name for p in cache_root.iterdir()) if cache_root.is_dir() else []
            missing = [
                f"no weight file (model.safetensors / pytorch_model.bin) found "
                f"anywhere under {cache_root}. cache_root_contents={top_level}"
            ]
            _log_cache_invalid(model_name, conventional_dir, missing)
            return False, missing, conventional_dir

        t_total = time.time() - t_verify_start
        weight_size_mb = sum(w.stat().st_size for w in weight_files) / 1024 / 1024
        top_level = sorted(p.name for p in cache_root.iterdir())

        print(
            f"[MODEL_CACHE_VERIFY] CACHE_OK\n"
            f"  strategy           : {strategy}\n"
            f"  resolved_model_dir : {resolved_dir}\n"
            f"  weight_files       : {[str(w) for w in weight_files]}\n"
            f"  weight_size        : {weight_size_mb:.1f} MB\n"
            f"  total_size         : {total_bytes / 1024 / 1024:.1f} MB\n"
            f"  cache_root_contents: {top_level}\n"
            f"  verification_time  : {t_total:.3f}s\n",
            flush=True,
        )
        return True, [], resolved_dir  # type: ignore[return-value]
    finally:
        print("[EXIT verify_cache()]", flush=True)
        logger.info("[EXIT verify_cache()]")


def _log_cache_invalid(model_name: str, model_dir: Path, missing: list[str]) -> None:
    msg = (
        f"[DOCKER_CACHE_INVALID] model={model_name} "
        f"model_dir={model_dir} missing={missing}. "
        f"The Docker image was built without the model or the cache path is wrong. "
        f"Rebuild the Docker image to fix this."
    )
    logger.error(msg)
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Torch diagnostics helper
# ---------------------------------------------------------------------------

def _log_torch_diagnostics() -> None:
    """Log torch version + CUDA status, apply thread limits."""
    try:
        import torch
        ver          = torch.__version__
        cuda_ver     = getattr(torch.version, "cuda", None)
        cuda_avail   = torch.cuda.is_available() if hasattr(torch, "cuda") else False
        is_cuda_build = cuda_ver is not None and str(cuda_ver) not in ("None", "")

        print(
            f"[TORCH_DIAGNOSTICS] version={ver} cuda_version={cuda_ver} "
            f"cuda_available={cuda_avail} cuda_build={is_cuda_build} "
            f"threads_before={torch.get_num_threads()}",
            flush=True,
        )

        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
    except Exception as exc:
        logger.warning("[TORCH_DIAGNOSTICS] failed: %s", exc)


def _import_dependencies_safe() -> None:
    """Import torch, transformers, and sentence_transformers safely.
    Delegates to the main thread's event loop to prevent OpenMP/PyTorch background thread deadlocks.
    """
    import sys
    if "torch" in sys.modules and "transformers" in sys.modules and "sentence_transformers" in sys.modules:
        return

    # Delegate imports to the main thread event loop if we are in a background thread
    if _main_loop is not None:
        try:
            if threading.current_thread() is not threading.main_thread():
                logger.info("[DELEGATE_IMPORT] delegating library imports to main thread event loop")
                print("[DELEGATE_IMPORT] delegating library imports to main thread event loop", flush=True)
                
                async def _coro_import():
                    import torch          # noqa: F401
                    import transformers   # noqa: F401
                    from sentence_transformers import SentenceTransformer  # noqa: F401
                
                future = asyncio.run_coroutine_threadsafe(_coro_import(), _main_loop)
                future.result()  # block background thread until main thread completes imports
                logger.info("[DELEGATE_IMPORT] main thread completed imports successfully")
                print("[DELEGATE_IMPORT] main thread completed imports successfully", flush=True)
                return
        except Exception as e:
            logger.warning("[DELEGATE_IMPORT_FAILED] falling back to local thread import: %s", e)
            print(f"[DELEGATE_IMPORT_FAILED] falling back to local thread import: {e}", flush=True)

    import torch          # noqa: F401
    import transformers   # noqa: F401
    from sentence_transformers import SentenceTransformer  # noqa: F401


# ---------------------------------------------------------------------------
# Core load function
# ---------------------------------------------------------------------------

def _do_load(model_name: str) -> None:
    """
    Loads the model through all phases. Executed either directly in the
    calling thread (lazy load path) or in a preload thread (lifespan path).
    """
    print("[ENTER _do_load()]", flush=True)
    logger.info("[ENTER _do_load()]")
    global _model, _model_name, _load_state, _load_error, _current_stage

    t0 = time.time()

    try:
        # 1. Set thread limits and offline environment settings
        _set_env_limits()
        _set_offline_mode()
        _set_hf_cache()

        _stage("START_LOAD", t0)
        logger.info(
            "[MODEL_SERVICE] [MODEL_LOAD_START] model=%s pid=%d",
            model_name, os.getpid(),
        )

        # 2. Verify Cache
        _stage("VERIFY_CACHE", t0)
        cache_ok, missing_files, model_dir = verify_docker_cache(model_name)
        if not cache_ok:
            raise ModelLoadFailed(
                f"Docker cache incomplete for model '{model_name}'. "
                f"Missing: {missing_files}. Network download disabled."
            )

        # 3. Check in-process cache
        try:
            from src.features.embedding import _MODEL_CACHE
            if model_name in _MODEL_CACHE:
                logger.info("[MODEL_SERVICE] [MODEL_CACHE_HIT] name=%s", model_name)
                _model = _MODEL_CACHE[model_name]
                _model_name = model_name
                with _lock:
                    _load_state = "loaded"
                _load_event.set()
                _stage("MODEL_READY", t0)
                return
        except Exception:
            pass

        # ── Silence noisy library loggers ──
        logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
        logging.getLogger("transformers").setLevel(logging.ERROR)
        logging.getLogger("sentence_transformers").setLevel(logging.WARNING)

        # 4. Load config / imports
        _stage("LOAD_CONFIG", t0)
        _import_dependencies_safe()
        _log_torch_diagnostics()

        # 5. Load tokenizer
        _stage("LOAD_TOKENIZER", t0)
        from sentence_transformers import SentenceTransformer

        # 6. Load model weights
        _stage("LOAD_MODEL_WEIGHTS", t0)
        st_home = os.environ["SENTENCE_TRANSFORMERS_HOME"]
        resolved_path = Path(st_home) / model_name.replace("/", "_")
        print(
            f"[MODEL_SERVICE] Resolved model path: {resolved_path}\n"
            f"[MODEL_SERVICE] cache_folder: {st_home}\n"
            f"[MODEL_SERVICE] device: cpu",
            flush=True,
        )

        loaded = SentenceTransformer(
            model_name,
            cache_folder=st_home,
            local_files_only=True,
            device="cpu",
        )

        # 7. Discover path from object & log modules
        t_discover = time.time()
        obj_path, obj_source = _discover_model_path_from_object(loaded)
        t_discover_elapsed = time.time() - t_discover
        if obj_path is not None:
            print(f"[MODEL_PATH_DISCOVERED] resolved_model_dir={obj_path} source={obj_source}", flush=True)

        _stage("BUILD_MODULES", t0)
        _stage("INITIALIZE_POOLING", t0)
        dim = loaded.get_sentence_embedding_dimension()

        elapsed = time.time() - t0
        rss_post = _rss_mb()
        print(
            f"[MODEL_SERVICE] [MODEL_LOAD_COMPLETE] model={model_name} "
            f"elapsed={elapsed:.1f}s embedding_dim={dim} RSS={rss_post:.1f}MB",
            flush=True,
        )

        # gc.collect to free any load-time temporaries
        gc.collect()

        # 8. Cache & Commit singleton
        try:
            from src.features.embedding import _MODEL_CACHE as _MC
            _MC[model_name] = loaded
        except Exception:
            pass

        _model = loaded
        _model_name = model_name
        with _lock:
            _load_state = "loaded"

        _stage("MODEL_READY", t0)
        print(f"[MODEL_SERVICE] [MODEL_SINGLETON_CREATED] name={model_name}", flush=True)

    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        logger.error(
            "[MODEL_SERVICE] [MODEL_LOAD_FAILED] model=%s stage=%s error=%s\n%s",
            model_name, _current_stage, exc, tb,
        )
        with _lock:
            _load_error = exc
            _load_state = "failed"
    finally:
        _load_event.set()
        print("[EXIT _do_load()]", flush=True)
        logger.info("[EXIT _do_load()]")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def preload(model_name: Optional[str] = None) -> bool:
    """Kicks off background model preload (lifespan startup context)."""
    global _load_state
    target = model_name or _get_model_name()
    with _lock:
        if _load_state in ("loaded", "loading"):
            return False
        _load_state = "loading"
        _load_event.clear()
    t = threading.Thread(target=_do_load, args=(target,), name="model-preload", daemon=True)
    t.start()
    return True


def get_model(timeout: float = MODEL_LOAD_TIMEOUT_SECONDS):
    """
    Returns the loaded SentenceTransformer.
    Loads directly in the calling thread if currently unloaded to prevent deadlock.
    """
    global _load_state, _load_error, _model

    target = _get_model_name()
    trigger_direct_load = False

    with _lock:
        if _load_state == "loaded" and _model is not None:
            return _model

        if _load_state == "failed":
            raise ModelLoadFailed(f"Model '{target}' failed: {_load_error}") from _load_error

        if _load_state == "unloaded":
            _load_state = "loading"
            _load_event.clear()
            trigger_direct_load = True

    if trigger_direct_load:
        logger.info(
            "[MODEL_SERVICE] [LAZY_LOAD_TRIGGERED] model=%s pid=%d (loading directly in calling thread)",
            target, os.getpid(),
        )
        print(
            f"[MODEL_SERVICE] [LAZY_LOAD_TRIGGERED] model={target} (direct load)",
            flush=True,
        )
        # Import the heavy packages in the calling thread BEFORE performing construction
        _import_dependencies_safe()
        
        # Load the model directly in the calling thread (avoiding thread join timeouts)
        _do_load(target)

    # Wait if it was loading concurrently (e.g. from background preloader)
    if _load_state == "loading":
        waited = 0.0
        heartbeat = 5.0
        while waited < timeout:
            chunk = min(heartbeat, timeout - waited)
            if _load_event.wait(timeout=chunk):
                break
            waited += chunk
            logger.info(
                "[MODEL_SERVICE] [WAIT] waited=%.0fs/%.0fs stage=%s RSS=%.1fMB",
                waited, timeout, _current_stage, _rss_mb(),
            )

        if not _load_event.is_set():
            exc = ModelLoadTimeout(
                f"Model '{target}' did not finish loading within {timeout}s. Last stage: {_current_stage}."
            )
            with _lock:
                _load_error = exc
                _load_state = "failed"
            raise exc

    with _lock:
        state   = _load_state
        error   = _load_error
        current = _model

    if state == "failed":
        raise ModelLoadFailed(f"Model '{target}' failed: {error}") from error
    if current is None:
        raise ModelLoadFailed(f"Model '{target}' returned None after load")

    return current


def is_loaded() -> bool:
    return _load_state == "loaded" and _model is not None


def get_model_name() -> Optional[str]:
    return _model_name


def get_load_state() -> str:
    return _load_state


def get_load_error() -> Optional[Exception]:
    return _load_error


def reset() -> None:
    """For tests only. Unloads model and resets state."""
    global _model, _model_name, _load_state, _load_error, _current_stage
    with _lock:
        _model = None
        _model_name = None
        _load_state = "unloaded"
        _load_error = None
        _current_stage = "idle"
        _load_event.clear()
    try:
        from src.features.embedding import _MODEL_CACHE
        _MODEL_CACHE.clear()
    except Exception:
        pass
    gc.collect()
    logger.info("[MODEL_SERVICE] reset() — model unloaded.")


# Configure offline mode and thread limits at module import time
_set_env_limits()
_set_offline_mode()
_set_hf_cache()
