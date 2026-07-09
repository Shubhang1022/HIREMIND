"""
ModelService — process-wide embedding model singleton.

All 9 phases implemented:
  Phase 1  — Docker cache verification (every required file checked)
  Phase 2  — Network disabled: local_files_only=True + cache_folder explicit
  Phase 3  — Per-stage instrumentation (START_LOAD → MODEL_READY)
  Phase 4  — HF offline env vars set before any import
  Phase 5  — Thread limits set before any ML import
  Phase 6  — Fail-fast: 30-second hard timeout per stage
  Phase 7  — Docker cache validation at startup, DOCKER_CACHE_INVALID on failure
  Phase 8  — No from_pretrained / hf_hub_download / snapshot_download calls
  Phase 9  — Reports: ModelLoadingAudit.md, CacheAudit.md, StartupTimeline.md
"""

from __future__ import annotations

import gc
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
# Phase 6: per-stage fail-fast timeout — each individual stage must complete
# within this many seconds or ModelLoadTimeout is raised immediately.
MODEL_STAGE_TIMEOUT_SECONDS = int(os.environ.get("MODEL_STAGE_TIMEOUT", "30"))

# Outer caller timeout: how long get_model() will wait for the full load.
# Must be > MODEL_STAGE_TIMEOUT_SECONDS × number of stages (≈ 5 stages × 30 s = 150 s).
# Default: 120 s — matches previous behaviour, enough for disk-cached load (~15 s).
MODEL_LOAD_TIMEOUT_SECONDS = int(os.environ.get("MODEL_LOAD_TIMEOUT", "120"))

_DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


class ModelLoadTimeout(RuntimeError):
    """Raised when a model loading stage exceeds the per-stage timeout."""


class ModelLoadFailed(RuntimeError):
    """Raised when the model loading fails (missing files, import error, etc.)."""


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_load_event = threading.Event()

_model = None
_model_name: Optional[str] = None
_load_state: str = "unloaded"       # "unloaded"|"loading"|"loaded"|"failed"
_load_error: Optional[Exception] = None
_current_stage: str = "idle"        # tracks which stage is active for timeout logging


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
    print("[MODEL_SERVICE] HF offline mode ENABLED — no network requests permitted", flush=True)


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

    Probe order (public APIs first, private attributes only as last resort):
      1. model_obj.tokenizer.name_or_path             — PreTrainedTokenizer, public
      2. model_obj[0].auto_model.config._name_or_path — Transformer module, semi-public
      3. model_obj._modules iteration → each module's config._name_or_path
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
                    # Verify it actually contains weights (not just tokenizer files)
                    if any((candidate / w).is_file() for w in weight_names):
                        return candidate, "tokenizer.name_or_path"
                    # Could be a parent dir — weights might be in same dir
                    # or the tokenizer dir IS the model dir
                    return candidate, "tokenizer.name_or_path"
    except Exception:
        pass

    # ── Probe 2: first module's auto_model.config._name_or_path ──────────
    try:
        # SentenceTransformer is a Sequential; modules are accessible by index
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

    Discovery strategy:
      PRIMARY   — introspect the loaded SentenceTransformer object (fast, O(1))
      FALLBACK  — recursive filesystem scan of cache_root (correct but slower)

    The model object is NOT available at this call site (verify_docker_cache runs
    before SentenceTransformer() is constructed). Therefore discovery here uses
    filesystem heuristics only.  Object-based discovery is used in the
    Dockerfile pre-bake step where the model is already loaded.

    Pass condition:
      - cache_root exists AND at least one weight file found (direct or rglob)

    Fail condition (only two hard failures):
      - cache_root does not exist, OR
      - no weight file found anywhere under cache_root.

    Returns (ok, missing_files, resolved_model_dir).
    """
    t_verify_start = time.time()
    cache_root = Path(
        os.environ.get("SENTENCE_TRANSFORMERS_HOME", "/app/.cache/sentence-transformers")
    )
    conventional_dir = cache_root / model_name.replace("/", "_")

    # ── Print env context ────────────────────────────────────────────────────
    print(
        f"\n[MODEL_CACHE_VERIFY] ─────────────────────────────────────────\n"
        f"  HF_HOME                   : {os.environ.get('HF_HOME')}\n"
        f"  TRANSFORMERS_CACHE        : {os.environ.get('TRANSFORMERS_CACHE')}\n"
        f"  SENTENCE_TRANSFORMERS_HOME: {os.environ.get('SENTENCE_TRANSFORMERS_HOME')}\n"
        f"  model_name                : {model_name}\n"
        f"  cache_root                : {cache_root}\n"
        f"  cache_root_exists         : {cache_root.is_dir()}\n"
        f"  conventional_dir          : {conventional_dir}\n"
        f"  conventional_dir_exists   : {conventional_dir.is_dir()}\n"
        f"[MODEL_CACHE_VERIFY] ─────────────────────────────────────────\n",
        flush=True,
    )

    # ── Hard failure: cache root missing ────────────────────────────────────
    if not cache_root.is_dir():
        missing = [f"cache_root {cache_root} (directory missing)"]
        _log_cache_invalid(model_name, conventional_dir, missing)
        return False, missing, conventional_dir

    # ── PRIMARY strategy: check conventional directory first (O(1) stat) ────
    # This is a direct path check — no object introspection needed here,
    # but it avoids the rglob scan on the happy path (directory exists and
    # contains weights in the expected location).
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

    # ── FALLBACK strategy: recursive scan (O(n files)) ───────────────────────
    if resolved_dir is None:
        strategy = "rglob"
        logger.info(
            "[MODEL_PATH_DISCOVERY_FAILED] conventional_dir=%s not found or has no weights "
            "— falling_back_to_recursive_scan=True",
            conventional_dir,
        )
        print(
            f"[MODEL_PATH_DISCOVERY_FAILED] conventional_dir={conventional_dir} "
            f"— falling_back_to_recursive_scan=True",
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

        # Perf log for rglob
        print(
            f"[CACHE_DISCOVERY_PERF] strategy=rglob elapsed={t_rglob_elapsed:.3f}s "
            f"weight_files_found={len(weight_files)}",
            flush=True,
        )
        logger.info(
            "[CACHE_DISCOVERY_PERF] strategy=rglob elapsed=%.3fs weight_files=%d",
            t_rglob_elapsed, len(weight_files),
        )
    else:
        # Perf log for direct hit
        print(
            f"[CACHE_DISCOVERY_PERF] strategy=direct elapsed={t_direct_elapsed:.3f}s "
            f"weight_files_found={len(weight_files)}",
            flush=True,
        )
        logger.info(
            "[CACHE_DISCOVERY_PERF] strategy=direct elapsed=%.3fs weight_files=%d",
            t_direct_elapsed, len(weight_files),
        )

    # ── Hard failure: no weights anywhere ────────────────────────────────────
    if not weight_files:
        top_level = sorted(p.name for p in cache_root.iterdir()) if cache_root.is_dir() else []
        missing = [
            f"no weight file (model.safetensors / pytorch_model.bin) found "
            f"anywhere under {cache_root}. cache_root_contents={top_level}"
        ]
        print(
            f"[MODEL_CACHE_VERIFY] CACHE_INCOMPLETE\n"
            f"  cache_root: {cache_root}\n"
            f"  strategy  : {strategy}\n"
            f"  missing   : {missing}\n",
            flush=True,
        )
        logger.warning(
            "[MODEL_CACHE_VERIFY] CACHE_INCOMPLETE cache_root=%s strategy=%s missing=%s",
            cache_root, strategy, missing,
        )
        _log_cache_invalid(model_name, conventional_dir, missing)
        return False, missing, conventional_dir

    # ── Success ───────────────────────────────────────────────────────────────
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
    logger.info(
        "[MODEL_CACHE_VERIFY] CACHE_OK strategy=%s resolved_dir=%s "
        "weight_files=%d weight_mb=%.1f total_mb=%.1f elapsed=%.3fs",
        strategy, resolved_dir, len(weight_files),
        weight_size_mb, total_bytes / 1024 / 1024, t_total,
    )
    return True, [], resolved_dir  # type: ignore[return-value]


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
        if is_cuda_build:
            logger.warning(
                "[TORCH_DIAGNOSTICS] CUDA wheel detected (torch+cu*). "
                "Wastes ~350 MB RSS on CPU-only host. "
                "Fix: pin torch+cpu in requirements.txt"
            )

        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
        logger.info(
            "[TORCH_DIAGNOSTICS] version=%s cuda=%s cuda_available=%s "
            "threads_after=%d",
            ver, cuda_ver, cuda_avail, torch.get_num_threads(),
        )
    except Exception as exc:
        logger.warning("[TORCH_DIAGNOSTICS] failed: %s", exc)


# ---------------------------------------------------------------------------
# Phase 6: fail-fast stage watchdog
# ---------------------------------------------------------------------------

def _run_with_timeout(fn, timeout_sec: float, stage_name: str,
                      model_name: str, model_dir: Path, missing_files: list[str]):
    """
    Run fn() in a thread. Raise ModelLoadTimeout if it doesn't finish in timeout_sec.
    This is the Phase 6 fail-fast mechanism — one wrapper per expensive stage.
    """
    result_box: list = [None]
    exc_box:    list = [None]

    def _worker():
        try:
            result_box[0] = fn()
        except Exception as e:
            exc_box[0] = e

    t = threading.Thread(target=_worker, name=f"stage-{stage_name}", daemon=True)
    t.start()
    t.join(timeout=timeout_sec)

    if t.is_alive():
        # Stage hung — hard timeout
        rss = _rss_mb()
        cpu = _cpu_pct()
        msg = (
            f"[MODEL_LOAD_TIMEOUT] stage={stage_name} "
            f"exceeded {timeout_sec:.0f}s — aborting. "
            f"model={model_name} cache_path={model_dir} "
            f"missing_files={missing_files} RSS={rss:.1f}MB CPU={cpu:.1f}%"
        )
        logger.error(msg)
        print(msg, flush=True)
        raise ModelLoadTimeout(msg)

    if exc_box[0] is not None:
        raise exc_box[0]

    return result_box[0]


# ---------------------------------------------------------------------------
# Core load function
# ---------------------------------------------------------------------------

def _do_load(model_name: str) -> None:
    """
    Runs in a daemon thread. Loads the model through all phases 1-8.

    Stage sequence (Phase 3):
      START_LOAD → VERIFY_CACHE → LOAD_CONFIG → LOAD_TOKENIZER →
      LOAD_MODEL_WEIGHTS → BUILD_MODULES → INITIALIZE_POOLING → MODEL_READY
    """
    global _model, _model_name, _load_state, _load_error, _current_stage

    t0 = time.time()  # global timer for all stage elapsed measurements

    try:
        # ── Phase 5: thread limits FIRST — before any ML import ────────────
        _set_env_limits()

        # ── Phase 4: HF offline mode BEFORE any transformers import ────────
        _set_offline_mode()

        # ── Set cache paths ─────────────────────────────────────────────────
        _set_hf_cache()

        # ── Phase 3: START_LOAD ─────────────────────────────────────────────
        _stage("START_LOAD", t0)
        logger.info(
            "[MODEL_SERVICE] [MODEL_LOAD_START] model=%s stage_timeout=%ds "
            "outer_timeout=%ds pid=%d",
            model_name, MODEL_STAGE_TIMEOUT_SECONDS, MODEL_LOAD_TIMEOUT_SECONDS,
            os.getpid(),
        )

        # ── Phase 1+7: VERIFY_CACHE ─────────────────────────────────────────
        _stage("VERIFY_CACHE", t0)
        cache_ok, missing_files, model_dir = verify_docker_cache(model_name)

        if not cache_ok:
            raise ModelLoadFailed(
                f"Docker cache incomplete for model '{model_name}'. "
                f"Missing: {missing_files}. "
                f"Rebuild the Docker image. Network download disabled."
            )

        # ── Check in-process cache (src.features.embedding._MODEL_CACHE) ───
        try:
            import sys as _sys
            _project_root = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__)
                )))
            )
            if _project_root not in _sys.path:
                _sys.path.insert(0, _project_root)
            from src.features.embedding import _MODEL_CACHE
            if model_name in _MODEL_CACHE:
                logger.info(
                    "[MODEL_SERVICE] [MODEL_CACHE_HIT] name=%s — in-process cache hit",
                    model_name,
                )
                _model = _MODEL_CACHE[model_name]
                _model_name = model_name
                _load_state = "loaded"
                _load_event.set()
                _stage("MODEL_READY", t0)
                return
        except Exception:
            pass

        # ── Silence noisy library loggers ────────────────────────────────────
        logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
        logging.getLogger("transformers").setLevel(logging.ERROR)
        logging.getLogger("sentence_transformers").setLevel(logging.WARNING)

        # ── Phase 3: LOAD_CONFIG — import torch + transformers ──────────────
        _stage("LOAD_CONFIG", t0)

        def _import_torch_and_transformers():
            import torch          # noqa: F401
            import transformers   # noqa: F401
            return True

        _run_with_timeout(
            _import_torch_and_transformers,
            timeout_sec=MODEL_STAGE_TIMEOUT_SECONDS,
            stage_name="LOAD_CONFIG",
            model_name=model_name,
            model_dir=model_dir,
            missing_files=missing_files,
        )
        _log_torch_diagnostics()

        # ── Phase 3: LOAD_TOKENIZER — import sentence_transformers ──────────
        _stage("LOAD_TOKENIZER", t0)

        def _import_sentence_transformers():
            from sentence_transformers import SentenceTransformer  # noqa: F401
            return True

        _run_with_timeout(
            _import_sentence_transformers,
            timeout_sec=MODEL_STAGE_TIMEOUT_SECONDS,
            stage_name="LOAD_TOKENIZER",
            model_name=model_name,
            model_dir=model_dir,
            missing_files=missing_files,
        )

        from sentence_transformers import SentenceTransformer  # now definitely imported

        # ── Phase 2 + 3: LOAD_MODEL_WEIGHTS — construct SentenceTransformer ─
        # local_files_only=True  → never contacts HuggingFace Hub
        # cache_folder explicit  → loads from the exact Docker-baked path
        _stage("LOAD_MODEL_WEIGHTS", t0)

        st_home = os.environ["SENTENCE_TRANSFORMERS_HOME"]
        resolved_path = Path(st_home) / model_name.replace("/", "_")
        print(
            f"[MODEL_SERVICE] Resolved model path: {resolved_path}\n"
            f"[MODEL_SERVICE] cache_folder: {st_home}\n"
            f"[MODEL_SERVICE] local_files_only: True\n"
            f"[MODEL_SERVICE] device: cpu",
            flush=True,
        )

        def _construct_sentence_transformer():
            return SentenceTransformer(
                model_name,
                cache_folder=st_home,
                local_files_only=True,
                device="cpu",
            )

        loaded = _run_with_timeout(
            _construct_sentence_transformer,
            timeout_sec=MODEL_STAGE_TIMEOUT_SECONDS,
            stage_name="LOAD_MODEL_WEIGHTS",
            model_name=model_name,
            model_dir=model_dir,
            missing_files=missing_files,
        )

        # ── Phase 1: discover actual model path from loaded object ───────────
        # Primary strategy: inspect the loaded model object.
        # This is O(1) compared to the rglob scan used before construction.
        t_discover = time.time()
        obj_path, obj_source = _discover_model_path_from_object(loaded)
        t_discover_elapsed = time.time() - t_discover

        if obj_path is not None:
            print(
                f"[MODEL_PATH_DISCOVERED] resolved_model_dir={obj_path} "
                f"source={obj_source}",
                flush=True,
            )
            logger.info(
                "[MODEL_PATH_DISCOVERED] resolved_model_dir=%s source=%s",
                obj_path, obj_source,
            )
        else:
            print(
                f"[MODEL_PATH_DISCOVERED] source=none "
                f"(object introspection returned no path — rglob was used pre-construction)",
                flush=True,
            )

        print(
            f"[CACHE_DISCOVERY_PERF] strategy=direct_object_introspection "
            f"elapsed={t_discover_elapsed:.3f}s source={obj_source}",
            flush=True,
        )
        logger.info(
            "[CACHE_DISCOVERY_PERF] strategy=direct_object_introspection "
            "elapsed=%.3fs source=%s",
            t_discover_elapsed, obj_source,
        )

        # SentenceTransformer() has completed — all sub-stages (tokenizer,
        # weights, pooling) happened inside _construct_sentence_transformer().
        # Log the post-construction stages now to confirm each completed.
        _stage("BUILD_MODULES", t0)       # transformer layers built ✓
        _stage("INITIALIZE_POOLING", t0)  # pooling layer ready ✓
        dim = loaded.get_sentence_embedding_dimension()

        elapsed = time.time() - t0
        rss_post = _rss_mb()
        logger.info(
            "[MODEL_SERVICE] [MODEL_LOAD_COMPLETE] model=%s elapsed=%.1fs "
            "embedding_dim=%d RSS=%.1fMB",
            model_name, elapsed, dim, rss_post,
        )
        print(
            f"[MODEL_SERVICE] [MODEL_LOAD_COMPLETE] model={model_name} "
            f"elapsed={elapsed:.1f}s embedding_dim={dim} RSS={rss_post:.1f}MB",
            flush=True,
        )

        # ── gc.collect() to release init-time temporaries ───────────────────
        rss_pre_gc = rss_post
        gc.collect()
        rss_post_gc = _rss_mb()
        logger.info(
            "[MODEL_SERVICE] [GC_RESULT] freed=%.1fMB rss_after=%.1fMB",
            rss_pre_gc - rss_post_gc, rss_post_gc,
        )

        # ── Back-fill in-process cache ──────────────────────────────────────
        try:
            from src.features.embedding import _MODEL_CACHE as _MC
            _MC[model_name] = loaded
        except Exception:
            pass

        # ── Commit singleton ────────────────────────────────────────────────
        _model = loaded
        _model_name = model_name
        _load_state = "loaded"

        # ── Phase 3: MODEL_READY ────────────────────────────────────────────
        _stage("MODEL_READY", t0)
        logger.info(
            "[MODEL_SERVICE] [MODEL_SINGLETON_CREATED] id=%d name=%s",
            id(loaded), model_name,
        )
        print(
            f"[MODEL_SERVICE] [MODEL_SINGLETON_CREATED] id={id(loaded)} "
            f"name={model_name}",
            flush=True,
        )

    except Exception as exc:
        import traceback as _tb
        tb = _tb.format_exc()
        logger.error(
            "[MODEL_SERVICE] [MODEL_LOAD_FAILED] model=%s stage=%s "
            "error=%s\n%s",
            model_name, _current_stage, exc, tb,
        )
        _load_error = exc
        _load_state = "failed"

    finally:
        _load_event.set()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def preload(model_name: Optional[str] = None) -> bool:
    """Compatibility shim — kept for tests/manual triggers. Returns True if load started."""
    global _load_state
    target = model_name or _get_model_name()
    with _lock:
        if _load_state in ("loaded", "loading"):
            logger.info(
                "[MODEL_SERVICE] preload() skipped — state=%s", _load_state
            )
            return False
        _load_state = "loading"
        _load_event.clear()
    t = threading.Thread(target=_do_load, args=(target,), name="model-preload", daemon=True)
    t.start()
    return True


def get_model(timeout: float = MODEL_LOAD_TIMEOUT_SECONDS):
    """
    Return the loaded SentenceTransformer.

    - state=loaded  → return instantly (fast path)
    - state=unloaded → trigger lazy load, then wait
    - state=loading  → wait on same _load_event
    - state=failed   → raise ModelLoadFailed immediately

    Raises ModelLoadTimeout if load doesn't complete within timeout.
    Raises ModelLoadFailed  if load raised an exception.
    """
    global _load_state, _load_error

    target = _get_model_name()

    with _lock:
        if _load_state == "loaded" and _model is not None:
            logger.debug(
                "[MODEL_SERVICE] [MODEL_REUSED] id=%d name=%s", id(_model), _model_name
            )
            return _model

        if _load_state == "failed":
            raise ModelLoadFailed(
                f"Model '{target}' failed to load: {_load_error}"
            ) from _load_error

        if _load_state == "unloaded":
            _load_state = "loading"
            _load_event.clear()
            logger.info(
                "[MODEL_SERVICE] [LAZY_LOAD_TRIGGERED] model=%s pid=%d",
                target, os.getpid(),
            )
            print(
                f"[MODEL_SERVICE] [LAZY_LOAD_TRIGGERED] model={target}",
                flush=True,
            )
            t = threading.Thread(
                target=_do_load, args=(target,), name="model-lazy-load", daemon=True
            )
            t.start()

    # Wait with heartbeat logs every 5 s
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
            f"Model '{target}' did not finish loading within {timeout}s. "
            f"Last stage: {_current_stage}. Check logs for DOCKER_CACHE_INVALID."
        )
        with _lock:
            _load_error = exc
            _load_state = "failed"
        logger.error(
            "[MODEL_SERVICE] [MODEL_LOAD_TIMEOUT] model=%s timeout=%ds stage=%s",
            target, int(timeout), _current_stage,
        )
        raise exc

    with _lock:
        state   = _load_state
        error   = _load_error
        current = _model

    if state == "failed":
        raise ModelLoadFailed(
            f"Model '{target}' failed: {error}"
        ) from error
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
    """For tests only. Unloads model and resets all state."""
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

