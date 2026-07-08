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
# ALL three vars point to the SAME root path used by model_service._set_hf_cache()
# at runtime.  Changing any one of these requires updating model_service.py too.
ENV HF_HOME=/app/.cache/huggingface \
    TRANSFORMERS_CACHE=/app/.cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/app/.cache/sentence-transformers \
    HF_HUB_DISABLE_SYMLINKS_WARNING=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TOKENIZERS_PARALLELISM=false

# ── Embedding model ────────────────────────────────────────────────────────────
# Must match: backend/app/core/config.py → embedding_model
#             backend/app/services/model_service.py → _DEFAULT_MODEL
ENV EMBEDDING_MODEL_NAME=BAAI/bge-small-en-v1.5

# Pre-download the production embedding model during image build.
# The downloaded files land in $SENTENCE_TRANSFORMERS_HOME which is
# /app/.cache/sentence-transformers — the same path model_service reads at runtime.
# Result: cold-start picks up cached weights immediately; no runtime download.
RUN python - <<'PY'
import os
from sentence_transformers import SentenceTransformer

model_name = os.environ.get("EMBEDDING_MODEL_NAME", "BAAI/bge-small-en-v1.5")
print(f"[Dockerfile] Pre-downloading {model_name} into {os.environ['SENTENCE_TRANSFORMERS_HOME']} ...")
m = SentenceTransformer(model_name, device="cpu")
dim = m.get_sentence_embedding_dimension()
print(f"[Dockerfile] Model cached. embedding_dim={dim}")
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