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
    pip install --no-cache-dir torch==2.2.2+cpu --extra-index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt

# Verify asyncpg installation at build time
RUN python -c "import asyncpg; print('asyncpg:', asyncpg.__version__)"

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
# Production defaults optimized for 512MB RAM deployments (Render free tier)
# Batch size will be dynamically adjusted based on environment detection:
#   - Free tier (512MB): batch_size=4, chunk_size=32
#   - Larger instances: batch_size=16, chunk_size=64
ENV EMBEDDING_MODEL_NAME=BAAI/bge-small-en-v1.5 \
    EMBEDDING_CHUNK_SIZE=64 \
    EMBEDDING_BATCH_SIZE=16 \
    UNLOAD_MODEL_AFTER_INDEXING=true \
    MEMORY_CIRCUIT_BREAKER_THRESHOLD_MB=400 \
    MEMORY_SAFETY_THRESHOLD_MB=450

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
import os, sys
from sentence_transformers import SentenceTransformer

model_name = os.environ.get("EMBEDDING_MODEL_NAME", "BAAI/bge-small-en-v1.5")
cache_folder = os.environ["SENTENCE_TRANSFORMERS_HOME"]

print(f"[MODEL_BAKE] Starting online model download/bake stage...")
print(f"[MODEL_BAKE] model={model_name} cache_folder={cache_folder}")

# ── Step A: Online Smoke Test (network enabled) ──────────────────────
try:
    model = SentenceTransformer(model_name, cache_folder=cache_folder, device="cpu")
    dim = model.get_sentence_embedding_dimension()
    
    # Generate smoke test embedding
    embedding = model.encode(
        ["HireMind embedding smoke test"],
        normalize_embeddings=True
    )
    
    assert embedding.shape[0] == 1, f"Expected 1 embedding, got {embedding.shape[0]}"
    assert embedding.shape[1] == dim, f"Expected dimension {dim}, got {embedding.shape[1]}"
    
    print(f"[MODEL_BAKE] Online load OK.")
    print(f"[MODEL_BAKE] embedding_dim={dim}")
    print(f"[MODEL_BAKE] Online smoke embedding successful.")
except Exception as exc:
    print(f"[MODEL_BAKE] FATAL: Online model bake failed: {exc}", file=sys.stderr)
    sys.exit(1)

# ── Step B: Offline Smoke Test (network disabled) ──────────────────────
print(f"[MODEL_BAKE] Starting offline model loading verification stage...")
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_DATASETS_OFFLINE"] = "1"

try:
    # Load same model with local_files_only=True to guarantee no network usage
    offline_model = SentenceTransformer(
        model_name,
        cache_folder=cache_folder,
        local_files_only=True,
        device="cpu"
    )
    
    dim_off = offline_model.get_sentence_embedding_dimension()
    assert dim_off == dim, f"Dimension mismatch: online={dim}, offline={dim_off}"
    
    embedding_off = offline_model.encode(
        ["HireMind offline embedding smoke test"],
        normalize_embeddings=True
    )
    
    assert embedding_off.shape[0] == 1
    assert embedding_off.shape[1] == dim
    
    print(f"[MODEL_BAKE] Offline load OK.")
    print(f"[MODEL_BAKE] Offline smoke embedding successful.")
    print(f"[MODEL_BAKE] cache ready.")
except Exception as exc:
    print(f"[MODEL_BAKE] FATAL: Offline model cache validation failed: {exc}", file=sys.stderr)
    sys.exit(1)

# Verify PyTorch CUDA settings
try:
    import torch
    cuda_avail = torch.cuda.is_available()
    print(f"[MODEL_BAKE] PyTorch CPU check: cuda_available={cuda_avail}")
    if cuda_avail:
        print("[MODEL_BAKE] WARNING: CUDA is available, but this container is configured for CPU-only execution.")
except Exception as exc:
    print(f"[MODEL_BAKE] PyTorch validation failed: {exc}", file=sys.stderr)
    sys.exit(1)

print("[MODEL_BAKE] Model baking and offline verification completed successfully!")
PY

# Copy application source AFTER pre-download (avoids re-download on code changes)
COPY backend/ /app/
COPY src/ /app/src/
COPY config/ /app/config/

ENV PYTHONPATH=/app \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1

EXPOSE 8000

# Railway-compatible startup — uses $PORT env var if set, falls back to 8000.
# Single worker: model singleton is shared; multiple workers would each load the
# model into memory, multiplying RSS. Scale horizontally via separate containers.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --log-level info"]