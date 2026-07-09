FROM python:3.10-slim

WORKDIR /app

# Prevent Python buffering and bytecode generation
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (Docker layer caching)
COPY backend/requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ── HuggingFace cache configuration ──────────────────────────────────────────
# These vars are read by model_service at runtime. Never override them in Railway.
ENV HF_HOME=/app/.cache/huggingface \
    TRANSFORMERS_CACHE=/app/.cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/app/.cache/sentence-transformers \
    HF_HUB_DISABLE_SYMLINKS_WARNING=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TOKENIZERS_PARALLELISM=false \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1

# ── Embedding model ────────────────────────────────────────────────────────────
# Must match: backend/app/core/config.py   → embedding_model
#             backend/app/services/model_service.py → _DEFAULT_MODEL
ENV EMBEDDING_MODEL_NAME=BAAI/bge-small-en-v1.5

# ── Pre-bake model weights into the image ─────────────────────────────────────
# Uses cache_folder=SENTENCE_TRANSFORMERS_HOME so the stored path matches what
# model_service uses at runtime with local_files_only=True.
#
# Verification strategy mirrors model_service.py:
#   PRIMARY  — inspect loaded model object for the actual cache path (fast)
#   FALLBACK — recursive cache_root scan (correct regardless of layout)
#
# Build FAILS only when:
#   1. SentenceTransformer() cannot be instantiated, OR
#   2. No weight file (model.safetensors / pytorch_model.bin) exists anywhere
#      under the configured cache root.
RUN python - <<'PY'
import os, sys, time
from pathlib import Path
from sentence_transformers import SentenceTransformer

model_name   = os.environ.get("EMBEDDING_MODEL_NAME", "BAAI/bge-small-en-v1.5")
cache_folder = os.environ["SENTENCE_TRANSFORMERS_HOME"]
cache_root   = Path(cache_folder)
WEIGHT_NAMES = {"model.safetensors", "pytorch_model.bin"}

print(f"[Dockerfile] Baking model={model_name} cache_folder={cache_folder} ...")

# ── Step 1: construct the model — hard failure if this raises ─────────────────
try:
    m = SentenceTransformer(model_name, cache_folder=cache_folder, device="cpu")
    dim = m.get_sentence_embedding_dimension()
    print(f"[Dockerfile] SentenceTransformer loaded OK. embedding_dim={dim}")
except Exception as exc:
    print(f"[Dockerfile] FATAL: SentenceTransformer() failed — {exc}", file=sys.stderr)
    sys.exit(1)

# ── Step 2: PRIMARY — discover path from the loaded model object ──────────────
# Mirrors _discover_model_path_from_object() in model_service.py.
# Uses only public / semi-public attributes; private attrs as last resort.
t0 = time.time()
obj_path = None
obj_source = "none"

# Probe 1: tokenizer.name_or_path
try:
    tok = getattr(m, "tokenizer", None)
    if tok is not None:
        nop = getattr(tok, "name_or_path", None)
        if nop:
            c = Path(nop)
            if c.is_dir():
                obj_path, obj_source = c, "tokenizer.name_or_path"
except Exception:
    pass

# Probe 2: modules[0].auto_model.config._name_or_path
if obj_path is None:
    try:
        first = m[0]
        auto  = getattr(first, "auto_model", None)
        if auto is not None:
            cfg = getattr(auto, "config", None)
            if cfg is not None:
                nop = getattr(cfg, "_name_or_path", None)
                if nop:
                    c = Path(nop)
                    if c.is_dir():
                        obj_path, obj_source = c, "modules[0].auto_model.config._name_or_path"
    except Exception:
        pass

# Probe 3: iterate _modules
if obj_path is None:
    try:
        for mname, mod in (getattr(m, "_modules", {}) or {}).items():
            for sub_attr in ("auto_model", "model"):
                sub = getattr(mod, sub_attr, None)
                if sub is None:
                    continue
                cfg = getattr(sub, "config", None)
                if cfg is None:
                    continue
                nop = getattr(cfg, "_name_or_path", None)
                if nop:
                    c = Path(nop)
                    if c.is_dir():
                        obj_path = c
                        obj_source = f"_modules[{mname!r}].{sub_attr}.config._name_or_path"
                        break
            if obj_path:
                break
    except Exception:
        pass

t_obj = time.time() - t0

if obj_path:
    print(f"[MODEL_PATH_DISCOVERED] resolved_model_dir={obj_path} source={obj_source}")
    print(f"[CACHE_DISCOVERY_PERF] strategy=direct elapsed={t_obj:.3f}s source={obj_source}")
else:
    print(f"[MODEL_PATH_DISCOVERED] source=none — falling back to recursive scan")
    print(f"[CACHE_DISCOVERY_PERF] strategy=direct elapsed={t_obj:.3f}s (no path found)")

# ── Step 3: FALLBACK — recursive scan if object discovery failed ──────────────
found_weights: list[Path] = []
total_mb = 0.0

if obj_path is not None:
    # Validate the discovered path
    wf = [obj_path / w for w in WEIGHT_NAMES if (obj_path / w).is_file()]
    if wf:
        found_weights = wf
        total_mb = sum(p.stat().st_size for p in obj_path.rglob("*") if p.is_file()) / 1024 / 1024
        strategy = "direct"
    else:
        # Path discovered but no weights there — still fall back
        print(f"[Dockerfile] Object path {obj_path} has no weights — falling back to rglob")

if not found_weights:
    t_rglob = time.time()
    all_cache_files: list[Path] = []
    for p in cache_root.rglob("*"):
        if p.is_file():
            all_cache_files.append(p)
            if p.name in WEIGHT_NAMES:
                found_weights.append(p)
    total_mb = sum(p.stat().st_size for p in all_cache_files) / 1024 / 1024
    strategy = "rglob"
    print(f"[CACHE_DISCOVERY_PERF] strategy=rglob elapsed={time.time()-t_rglob:.3f}s "
          f"files_scanned={len(all_cache_files)}")

# ── Step 4: log results ────────────────────────────────────────────────────────
print(f"[Dockerfile] cache_root          : {cache_root}")
print(f"[Dockerfile] cache_root_exists   : {cache_root.is_dir()}")
print(f"[Dockerfile] discovery_strategy  : {strategy}")
print(f"[Dockerfile] weight_files_found  : {[str(p) for p in found_weights]}")
print(f"[Dockerfile] total_size          : {total_mb:.1f} MB")
print(f"[Dockerfile] embedding_dim       : {dim}")

if cache_root.is_dir():
    top = sorted(str(p.relative_to(cache_root)) for p in cache_root.iterdir())
    print(f"[Dockerfile] cache_root contents : {top}")

# ── Step 5: pass/fail ──────────────────────────────────────────────────────────
if not found_weights:
    print(
        f"[Dockerfile] ERROR: model loaded but no weight file found under {cache_root}.\n"
        f"  SENTENCE_TRANSFORMERS_HOME may point to a different location than where\n"
        f"  SentenceTransformer wrote the weights. Check ENV in the Dockerfile.",
        file=sys.stderr,
    )
    sys.exit(1)

resolved_model_dir = found_weights[0].parent
print(f"[Dockerfile] resolved_model_dir  : {resolved_model_dir}")
print(f"[Dockerfile] Cache verified OK — model loaded, weights present. embedding_dim={dim}")
PY

# Copy application source AFTER pre-download (avoids re-download on code changes)
COPY backend/ /app/
COPY src/ /app/src/
COPY config/ /app/config/

ENV PYTHONPATH=/app

EXPOSE 8000

# Railway-compatible startup — uses $PORT env var if set, falls back to 8000.
# Single worker: model singleton is shared; multiple workers would each load the
# model into memory, multiplying RSS. Scale horizontally via separate containers.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --log-level info"]