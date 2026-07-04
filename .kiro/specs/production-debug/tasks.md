# Production Debugging Tasks

## Overview

Debug the HireMind production application to fix all runtime issues without changing architecture. The system uses FastAPI backend, Supabase (PostgreSQL + Storage), SQLAlchemy, Next.js frontend, and OpenRouter for LLM calls. Goal: make the end-to-end flow (Login → Project → Upload → Background Job → Embeddings → FAISS → Analysis → Rankings → Export) work without manual intervention.

**CRITICAL CONSTRAINT**: Do NOT redesign, migrate, or refactor. Only apply minimal targeted fixes.

## Tasks

- [ ] 1. End-to-End Flow Audit — generate FlowAudit.md
  - Read `backend/app/main.py`, `backend/app/api/v1/router.py`, all endpoint files under `backend/app/api/v1/endpoints/`
  - Read `backend/app/services/job_manager.py`, `backend/app/services/storage_provider.py`, `backend/app/services/cache_service.py`
  - Read `backend/app/core/config.py`, `backend/app/core/auth.py`, `backend/app/core/openrouter.py`
  - Read `backend/app/middleware/rate_limit.py`
  - Read `frontend/src/lib/platform-api.ts` to understand all API calls made from frontend
  - Read `frontend/src/app/(dashboard)/projects/[id]/page.tsx` and all dashboard pages
  - Trace every step: Login → Create Project → Upload JD → Upload Candidate Dataset → Create Background Job → Generate Embeddings → Build FAISS → Run Analysis → Generate Rankings → Analytics → Export
  - For each step document: function executed, API endpoint, database queries, storage operations, possible failure points
  - Generate `FlowAudit.md` at project root with complete trace

- [ ] 2. Candidate Upload Audit
  - Read the upload endpoint in `backend/app/api/v1/endpoints/`
  - Read `backend/app/services/storage_provider.py` for storage operations
  - Read `backend/app/services/job_manager.py` for background job creation after upload
  - Trace: POST upload endpoint → Supabase insert → Storage upload → Project status update → Background job creation → candidate count → dataset hash → version update
  - Identify any missing null checks, wrong status transitions, race conditions, or missing error handling
  - Apply minimal targeted fixes only

- [ ] 3. Background Job Audit — generate BackgroundWorkerAudit.md
  - Read `backend/app/services/job_manager.py` completely
  - Trace all state transitions: queued → processing → embedding → indexing → completed
  - Identify: stuck jobs, missing heartbeat, missing retry logic, thread safety issues, cancellation handling
  - Check that background threads properly handle exceptions without silently swallowing them
  - Generate `BackgroundWorkerAudit.md` at project root
  - Apply minimal fixes to ensure jobs never get permanently stuck

- [ ] 4. Embedding Audit — generate EmbeddingAudit.md
  - Read `backend/app/services/job_manager.py` embedding sections
  - Read `backend/app/services/cache_service.py`
  - Verify: SentenceTransformer loading, embedding batching, memory cleanup, HF cache, embedding persistence, NumPy output, FAISS creation
  - Check for silent failures and metadata fallback behavior
  - Generate `EmbeddingAudit.md` at project root
  - Apply minimal fixes for any embedding failures found

- [ ] 5. Analysis Audit — generate AnalysisFailureReport.md
  - Read `backend/app/api/v1/endpoints/` analysis/ranking endpoint
  - Read `backend/app/services/job_manager.py` analysis sections
  - Read `backend/app/core/openrouter.py`
  - Trace: embedding exists check → FAISS exists check → project exists check → candidate mapping exists check → ranking generation → OpenRouter call → LLM scoring → result persistence
  - Identify why analysis stops or fails
  - Generate `AnalysisFailureReport.md` at project root
  - Apply minimal fixes

- [ ] 6. API Audit — generate APIAudit.md
  - Read all endpoint files under `backend/app/api/v1/endpoints/`
  - Read `backend/app/api/v1/router.py`
  - Verify each endpoint: HTTP status codes, exception handling, input validation, timeout handling, background task interaction
  - Check: Projects API, Upload API, Analysis API, Health API, Ranking API, Exports API
  - Generate `APIAudit.md` at project root
  - Apply minimal fixes for broken exception handling or wrong HTTP status codes

- [ ] 7. Storage Audit — generate StorageAudit.md
  - Read `backend/app/services/storage_provider.py`
  - Read `backend/app/services/cache_service.py`
  - Verify every expected file path: candidate json, candidate mapping, embeddings, faiss index, role index, skill index, reports
  - Identify mismatched paths between where files are written and where they are read
  - Generate `StorageAudit.md` at project root
  - Apply minimal fixes for path mismatches

- [ ] 8. Render Stability Audit — generate RenderStabilityReport.md
  - Read `backend/app/main.py` for startup/shutdown lifecycle
  - Read `backend/app/services/job_manager.py` for thread management
  - Read `backend/app/middleware/rate_limit.py`
  - Identify: memory leaks, thread leaks, blocking code in async context, infinite loops, large allocations, OOM risk, improper worker lifecycle
  - Generate `RenderStabilityReport.md` at project root
  - Apply minimal fixes for thread leaks and blocking operations

- [ ] 9. Supabase Audit — generate SupabaseAudit.md
  - Read `backend/app/schemas/create_supabase_schema.sql`
  - Read `backend/app/core/config.py` for Supabase config
  - Read `backend/app/services/storage_provider.py` for storage bucket usage
  - Verify: tables exist and match code expectations, RLS policies, storage buckets, indexes, background job table, projects table, candidate uploads table, ranking tables
  - Do NOT modify schema unless a real mismatch exists between SQL schema and Python code
  - Generate `SupabaseAudit.md` at project root

- [ ] 10. Frontend Audit — generate FrontendAudit.md
  - Read `frontend/src/lib/platform-api.ts`
  - Read all dashboard pages under `frontend/src/app/(dashboard)/`
  - Read `frontend/src/components/upload/`, `frontend/src/components/candidates/`, `frontend/src/components/dashboard/`
  - Read `frontend/.env.local` for API base URL config
  - For every frontend API call verify it targets an existing backend endpoint
  - Detect broken API calls, wrong HTTP methods, missing auth headers, wrong payload shapes
  - Generate `FrontendAudit.md` at project root
  - Apply minimal fixes for broken API calls

- [ ] 11. Root Cause Report — generate RootCauseReport.md
  - Synthesize findings from Tasks 1–10
  - For every discovered issue document: Problem → Root Cause → Affected File → Affected Function → Severity (critical/high/medium/low) → Recommended Fix → Estimated Risk
  - Generate `RootCauseReport.md` at project root

- [ ] 12. Apply Minimal Fixes + generate ProductionFixReport.md
  - Based on RootCauseReport.md apply ONLY: broken imports, incorrect API calls, wrong status transitions, missing null checks, exception handling, race conditions, incorrect Supabase queries, incorrect storage paths, thread synchronization, memory leaks
  - Do NOT refactor, redesign, or migrate
  - Generate `ProductionFixReport.md` at project root documenting every file changed, what was changed, and why

## Task Dependency Graph

```json
{
  "waves": [
    {"wave": 1, "tasks": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]},
    {"wave": 2, "tasks": ["11"]},
    {"wave": 3, "tasks": ["12"]}
  ]
}
```

## Notes

- Do NOT redesign architecture, migrate databases, remove SQLAlchemy, remove Supabase, or delete endpoints
- Do NOT clean up legacy code unrelated to bugs
- Apply only minimal, targeted fixes to make the existing application work
- All audit documents go to the project root directory
- Supabase schema has already been synchronized — only fix real mismatches found in code
