# HireMind AI — AI Recruiter Copilot

**Production-ready, highly-scalable AI hiring platform. Ingest candidate datasets in any format and rank talent using HNSW vector retrieval, multi-signal scoring, and batch LLM explainable evaluations.**

---

## 🚀 Key Features & Capabilities

- **Row-Level Security (RLS) Auth**: Complete tenant isolation powered by Supabase Auth and database level policies (`auth.uid() = user_id`).
- **Generic Ingestion Engine**: Supports PDF, DOCX, CSV, Excel (XLS/XLSX), JSON, and JSONL bulk resume packages with automatic schema inference (no column mapping required).
- **Scale-Hardened JD-First Pipeline**: Optimized to easily handle large datasets (100k+ candidates) in under 60 seconds:
  1. **Immediate Job Profiling**: Extracts minimum experience, required skills, and role categories from the Job Description.
  2. **Streaming Metadata Prefilter**: Streams and filters candidate files with $O(1)$ memory overhead, dropping non-matching candidates on the fly.
  3. **In-Memory HNSW Vector Search**: Retains the top pre-filtered candidates, queries semantic similarity using the BGE-large embedding model, and retrieves top results using an in-memory FAISS vector index.
  4. **Batch LLM Evaluation**: Passes summaries of the top candidates in a single optimized payload to OpenRouter (`google/gemini-2.5-flash`), generating strengths, weaknesses, risks, interview questions, and recommendation statuses.
- **Resilient Fallback Modes**:
  - **Metadata-Only Fallback**: Automatically bypasses vector search if background embedding generation is pending, running deterministic metadata analysis instead.
  - **Deterministic LLM Fallback**: Gracefully recovers if OpenRouter APIs timeout or return 402/429/5xx errors, serving structured rankings without throwing 500 error codes.
- **Flexible Recruiter Dashboard**: Integrates candidate drawer views, multi-dimensional score grids, vacancy limits, "Recommended" vs. "Backup" labels, skill coverage calculations, and downloadable Excel and CSV summaries.

---

## 📦 Production Architecture

```
┌────────────────────────┐      ┌─────────────────────────┐      ┌─────────────────────────┐
│       Next.js 16       │ ───> │     FastAPI Backend     │ ───> │        Supabase         │
│   Recruiter Frontend   │      │    (AI & Ingestion)     │      │   (PostgreSQL + RLS)    │
└────────────────────────┘      └─────────────────────────┘      └─────────────────────────┘
                                             │                                │
                                             ▼                                ▼
                                ┌─────────────────────────┐      ┌─────────────────────────┐
                                │     In-Memory Cache     │      │    Supabase Storage     │
                                │   (FAISS, Embeddings)   │      │    (Object Buckets)     │
                                └─────────────────────────┘      └─────────────────────────┘
```

### 1. Pure In-Memory Operations
To support deployment on ephemeral file systems (like Render or Heroku), the backend has been refactored to bypass the local disk during querying and indexing:
* **FAISS Loading**: Indicies are serialized and deserialized directly from bytes in RAM using `faiss.serialize_index` and `faiss.deserialize_index(np.frombuffer(...))`.
* **NumPy Arrays**: Saved and loaded directly from byte streams using `io.BytesIO`.

### 2. Storage Service Abstraction Layer
A unified `StorageService` interface delegates operations to either `LocalStorageProvider` or `SupabaseStorageProvider` based on the `USE_SUPABASE_STORAGE` feature flag, keeping endpoints clean and decoupled.

---

## 🔒 Security & Git Safety

The project is pre-configured with a strict [.gitignore](file:///.gitignore) file to ensure sensitive candidate information and private credentials are never pushed to GitHub:

### Gitignored Files:
- **Environment Files**: `.env` and `.env.local` containing API keys and database passwords.
- **Local Data & Database**: `data/` folder containing local sqlite/JSON files, raw candidate packages, and local embeddings.
- **Diagnostic Scripts & Verification MDs**: Local notebooks, presentations, and draft markdown specifications (e.g. `DemoScript.md`, `WorkflowRefactorImplementation.md`, `test_analyze.py`, etc.).
- **Temporary Outputs**: CSV exports and analysis JSON audit reports.

---

## 🚀 Installation & Local Startup

### Prerequisites
- Node.js 20+
- Python 3.10+
- Supabase Project

### 1. Supabase Initialization
1. Create a project at [supabase.com](https://supabase.com).
2. Open the **SQL Editor** and run the contents of [create_supabase_schema.sql](file:///backend/app/schemas/create_supabase_schema.sql). This will generate the required tables, initialize storage buckets, and set up Row-Level Security (RLS) policies.
3. Enable Email Auth under **Authentication** > **Providers** in the Supabase Dashboard.

### 2. Configure Environment Files

Create `frontend/.env.local`:
```env
NEXT_PUBLIC_SUPABASE_URL=https://okhxqdmajbibloxuhquy.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key
NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
```

Create `backend/.env`:
```env
APP_ENV=development
SECRET_KEY=generate-a-long-random-string
CORS_ORIGINS=http://localhost:3000

# Database Transaction Pooler
DATABASE_URL=postgresql+asyncpg://postgres.okhxqdmajbibloxuhquy:[PASSWORD]@aws-1-ap-southeast-2.pooler.supabase.com:6543/postgres

# OpenRouter AI
OPENROUTER_API_KEY=your-openrouter-api-key
OPENROUTER_MODEL=google/gemini-2.5-flash
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# Supabase Storage & Keys
SUPABASE_URL=https://okhxqdmajbibloxuhquy.supabase.co
SUPABASE_SERVICE_KEY=your-supabase-service-key
SUPABASE_JWT_SECRET=your-supabase-jwt-secret

# Feature Flags
USE_SUPABASE_STORAGE=true
USE_LOCAL_STORAGE=false
USE_FAISS_CACHE=true
USE_BACKGROUND_INDEXING=true
USE_OPENROUTER=true
```

### 3. Install and Start Services

#### Backend:
```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

#### Frontend (New Terminal):
```bash
cd frontend
npm install
npm run dev
```

#### Run One-Time Legacy Migration:
To migrate any existing candidate packages and database files from a local workspace to Supabase, execute:
```bash
python backend/scripts/migrate_to_supabase.py
```
This creates a local `migration.lock` file and writes to the DB history table to prevent duplicate migrations.

---

## 🧪 Testing & Verification

### Running Unit Tests
To run the full backend unit test suite, execute:
```bash
python -m pytest tests/
```

### Running E2E Workflow Script
To trigger a mock end-to-end analysis on the running backend server:
```bash
python C:\Users\HP\.gemini\antigravity-ide\brain\e82bd74c-acf3-4419-a8cd-85e7eb78506f\scratch\trigger_test_analyze.py
```

---

## 🌐 Production Deployment Guide

We strongly recommend hosting the frontend and backend **separately** due to different runtime requirements (Node.js vs. Python ML packages).

### 1. Frontend (Vercel)
- Set **Root Directory** to `frontend`.
- Set **Environment Variables**:
  - `NEXT_PUBLIC_SUPABASE_URL`
  - `NEXT_PUBLIC_SUPABASE_ANON_KEY`
  - `NEXT_PUBLIC_API_URL` (points to the Render backend URL)
- Deploy. Vercel will automatically compile the Next.js bundle and expose it via a global CDN.

### 2. Backend (Render)
- Deploy as a **Web Service** linked to your GitHub repository.
- Set **Root Directory** to `backend`.
- Set **Build Command**: `pip install -r requirements.txt`
- Set **Start Command**: `python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Define the production variables (including `OPENROUTER_API_KEY`, `DATABASE_URL`, `SUPABASE_SERVICE_KEY`, and feature flags) in the Render Env dashboard.
- Update `CORS_ORIGINS` in your backend configuration to include your live Vercel frontend URL.
