# APIAudit.md — API Endpoint Audit

## Backend API Surface (`/api/v1/platform/`)

| Method | Path | Handler | Status | Notes |
|--------|------|---------|--------|-------|
| GET | `/platform/projects` | `list_projects` | ✅ OK | Returns all projects for user |
| POST | `/platform/projects` | `create_project` | ✅ OK | Deduplication by name+user |
| GET | `/platform/projects/{id}` | `get_project` | ✅ OK | 404 if not found |
| PATCH | `/platform/projects/{id}` | `update_project` | ✅ OK | Partial update |
| DELETE | `/platform/projects/{id}` | `delete_project` | ✅ OK | Cascades storage deletion |
| POST | `/platform/projects/{id}/upload` | `upload_file` | ✅ OK | `upload_type=candidates\|job_description` |
| GET | `/platform/projects/{id}/candidates` | `list_candidates` | ✅ OK | Paginated; O(N) full-scan per page |
| GET | `/platform/projects/{id}/candidates/{cid}` | `get_candidate` | ✅ OK | Full candidate object |
| GET | `/platform/projects/{id}/jobs` | `list_jobs` | ✅ OK | Returns all JDs for project |
| POST | `/platform/projects/{id}/jobs` | `create_job` | ✅ **ADDED** | Was missing — caused 405 for "Paste JD" flow |
| POST | `/platform/projects/{id}/analyze` | `run_analysis` | ✅ OK | Guards: embedding_status, artifacts |
| GET | `/platform/projects/{id}/rankings/{rid}` | `get_ranking` | ✅ OK | Returns ranking with results |
| GET | `/platform/projects/{id}/analytics` | `get_analytics` | ✅ OK | Optional `ranking_id` param |
| POST | `/platform/projects/{id}/export` | `export_results` | ✅ OK | CSV/XLSX/PDF |
| GET | `/platform/projects/{id}/worker-status` | `get_worker_status` | ✅ OK | In-memory + DB fallback |
| GET | `/platform/projects/{id}/progress-stream` | `get_progress_stream` | ✅ OK | SSE stream |
| POST | `/platform/projects/{id}/cancel-indexing` | `cancel_indexing` | ✅ OK | Sets cancellation token |
| GET | `/platform/health-stats` | `get_health_stats` | ✅ OK | Aggregate stats |
| GET | `/platform/projects/{id}/performance-metrics` | `get_performance_metrics` | ✅ OK | |

## Health Endpoints

| Method | Path | Status | Notes |
|--------|------|--------|-------|
| GET | `/health` | ✅ OK | DB + Storage + Model + Memory status |
| GET | `/health/cors` | ✅ OK | CORS diagnostic |
| GET | `/api/v1/health` | ✅ OK | Extended health with faiss/dependencies |
| GET | `/api/v1/health/metrics` | ✅ OK | Prometheus-style metrics |
| GET | `/` | ✅ OK | `{"status":"healthy"}` |

---

## Frontend API Calls vs. Backend Endpoints

| Frontend Call | Backend Endpoint | Match |
|---------------|-----------------|-------|
| `platformApi.projects.list()` | `GET /platform/projects` | ✅ |
| `platformApi.projects.get(id)` | `GET /platform/projects/{id}` | ✅ |
| `platformApi.projects.create(data)` | `POST /platform/projects` | ✅ |
| `platformApi.projects.update(id, data)` | `PATCH /platform/projects/{id}` | ✅ |
| `platformApi.projects.delete(id)` | `DELETE /platform/projects/{id}` | ✅ |
| `platformApi.jobs.list(projectId)` | `GET /platform/projects/{id}/jobs` | ✅ |
| `platformApi.jobs.create(projectId, data)` | `POST /platform/projects/{id}/jobs` | ✅ **FIXED** |
| `platformApi.upload(projectId, file, 'candidates')` | `POST /platform/projects/{id}/upload?upload_type=candidates` | ✅ |
| `platformApi.upload(projectId, file, 'job_description')` | `POST /platform/projects/{id}/upload?upload_type=job_description` | ✅ |
| `platformApi.analyze(projectId, jobId, topK, mode)` | `POST /platform/projects/{id}/analyze` | ✅ |
| `platformApi.ranking(projectId, rankingId)` | `GET /platform/projects/{id}/rankings/{rid}` | ✅ |
| `platformApi.analytics(projectId, rankingId)` | `GET /platform/projects/{id}/analytics` | ✅ |
| `platformApi.export(projectId, rankingId, format)` | `POST /platform/projects/{id}/export` | ✅ |
| `platformApi.cancelIndexing(projectId)` | `POST /platform/projects/{id}/cancel-indexing` | ✅ |
| `platformApi.workerStatus(projectId)` | `GET /platform/projects/{id}/worker-status` | ✅ |
| `platformApi.healthStats()` | `GET /platform/health-stats` | ✅ |
| SSE stream URL (frontend `EventSource`) | `GET /platform/projects/{id}/progress-stream` | ✅ **FIXED** (double /api/v1 removed) |
| `platformApi.candidates.list(projectId)` | `GET /platform/projects/{id}/candidates` | ✅ |
| `platformApi.candidates.get(projectId, candidateId)` | `GET /platform/projects/{id}/candidates/{cid}` | ✅ |

---

## HTTP Status Codes Audit

| Code | Usage | Correct |
|------|-------|---------|
| 200 | Default success | ✅ |
| 201 | `create_project`, `create_job` | ✅ |
| 204 | `delete_project` | ✅ |
| 400 | Bad file format | ✅ |
| 401 | Invalid/missing JWT | ✅ |
| 404 | Project/job/candidate not found | ✅ |
| 409 | Concurrent analysis; indexing in progress; missing artifacts | ✅ |
| 422 | File parse failure; validation error | ✅ |
| 504 | Analysis timeout | ✅ |

---

## Validation & Timeout Summary

| Endpoint | Validation | Timeout |
|----------|------------|---------|
| `upload` (candidates) | File parse; zero-record check | No hard timeout; depends on file size |
| `analyze` | 5-guard pre-flight before any work | 60s hard limit via `check_overall_timeout()` |
| `export` | `ranking_id` existence check | Streaming response; no hard limit |
| SSE stream | Project existence check | Closes automatically on terminal status |

---

## Broken API Call Fixed

**Issue**: `POST /platform/projects/{id}/jobs` returned 405 Method Not Allowed  
**Impact**: "Paste / Type JD" flow in the frontend was completely broken — users could never create a job via text input, only via file upload  
**Fix**: Added `create_job()` endpoint handler with full LLM parsing pipeline  
**Risk**: Low — purely additive, no existing behavior changed
