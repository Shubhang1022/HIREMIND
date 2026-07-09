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
# Uses cache_folder=SENTENCE_TRANSFORMERS_HOME so the stored path exactly matches
# what model_service uses at runtime with local_files_only=True.
# After this RUN step, verify the files landed in the right place.
RUN python - <<'PY'
import os, sys
from pathlib import Path
from sentence_transformers import SentenceTransformer

model_name  = os.environ.get("EMBEDDING_MODEL_NAME", "BAAI/bge-small-en-v1.5")
cache_folder = os.environ["SENTENCE_TRANSFORMERS_HOME"]

print(f"[Dockerfile] Baking model={model_name} into cache_folder={cache_folder} ...")
m = SentenceTransformer(
    model_name,
    cache_folder=cache_folder,
    device="cpu",
)
dim = m.get_sentence_embedding_dimension()
print(f"[Dockerfile] Model baked. embedding_dim={dim}")

# Verify the files are where model_service will look for them
sanitized  = model_name.replace("/", "_")
model_dir  = Path(cache_folder) / sanitized
all_files  = sorted(p.name for p in model_dir.iterdir()) if model_dir.is_dir() else []
total_mb   = sum(p.stat().st_size for p in model_dir.rglob("*") if p.is_file()) / 1024 / 1024

REQUIRED = ["config.json", "tokenizer.json", "modules.json",
            "tokenizer_config.json", "special_tokens_map.json"]
WEIGHTS  = ["model.safetensors", "pytorch_model.bin"]

missing = [f for f in REQUIRED if f not in all_files]
has_weights = any(w in all_files for w in WEIGHTS)
if not has_weights:
    missing.append("weights (model.safetensors or pytorch_model.bin)")

print(f"[Dockerfile] cache_folder   : {cache_folder}")
print(f"[Dockerfile] model_dir      : {model_dir}")
print(f"[Dockerfile] model_dir exists: {model_dir.is_dir()}")
print(f"[Dockerfile] files          : {all_files}")
print(f"[Dockerfile] total_size     : {total_mb:.1f} MB")
print(f"[Dockerfile] missing        : {missing}")

if missing:
    print(f"[Dockerfile] ERROR: cache incomplete — {missing}", file=sys.stderr)
    sys.exit(1)

print(f"[Dockerfile] Cache verified OK. {len(all_files)} files, {total_mb:.1f} MB.")
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