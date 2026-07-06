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

# HuggingFace cache configuration
ENV HF_HOME=/app/.cache/huggingface \
    TRANSFORMERS_CACHE=/app/.cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/app/.cache/sentence-transformers \
    HF_HUB_DISABLE_SYMLINKS_WARNING=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TOKENIZERS_PARALLELISM=false

# Embedding model configuration
ENV EMBEDDING_MODEL_NAME=BAAI/bge-small-en-v1.5

# Pre-download the production embedding model during image build
RUN python - <<'PY'
from sentence_transformers import SentenceTransformer
print("Downloading embedding model...")
SentenceTransformer("BAAI/bge-small-en-v1.5", device="cpu")
print("Embedding model cached successfully.")
PY

# Copy application source
COPY backend/ /app/
COPY src/ /app/src/
COPY config/ /app/config/

ENV PYTHONPATH=/app

EXPOSE 8000



# Start FastAPI
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]