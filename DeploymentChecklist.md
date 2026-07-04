# DeploymentChecklist.md — HireMind Production Deployment

## Pre-Deploy: Environment Variables

Set all of these in Render Dashboard → Environment before deploying.

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `SUPABASE_URL` | ✅ YES | Supabase project URL | `https://xyz.supabase.co` |
| `SUPABASE_SERVICE_KEY` | ✅ YES | Service role key (bypasses RLS) | `sb_secret_...` |
| `SUPABASE_JWT_SECRET` | ✅ YES | From Supabase Dashboard → Settings → API → JWT Secret | 32+ char string |
| `OPENROUTER_API_KEY` | ✅ YES | OpenRouter API key for LLM scoring | `sk-or-v1-...` |
| `CORS_ORIGINS` | ✅ YES | Frontend URL(s), comma-separated | `https://hiremind-gilt.vercel.app` |
| `EMBEDDING_MODEL_NAME` | ⚠ Recommended | Override default model | `BAAI/bge-base-en-v1.5` |
| `HF_HOME` | ⚠ Recommended | HuggingFace cache path | `/app/.cache/huggingface` |
| `MODEL_LOAD_TIMEOUT` | Optional | Seconds before model load fails | `120` |
| `WATCHDOG_TIMEOUT_MINUTES` | Optional | Minutes before job is watchdog-killed | `2` |
| `EMBEDDING_MEM_ABORT_MB` | Optional | RSS threshold to abort embedding | `480` |

**Validation**: At startup, missing `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, or `OPENROUTER_API_KEY` will be logged as `[STARTUP_ERROR]` and summarised in `[STARTUP_SUMMARY]`. The process will still start but will degrade gracefully.

---

## Pre-Deploy: Supabase

- [ ] Run `supabase/migrations/001_initial_schema.sql` in SQL Editor if fresh project
- [ ] Run `supabase/migrations/002_background_jobs_user_id.sql` (idempotent, safe to re-run)
- [ ] Verify all 8 storage buckets exist: `candidate-files`, `embeddings`, `faiss-indexes`, `role-indexes`, `skill-indexes`, `exports`, `candidate-resumes`, `audit-reports`
- [ ] Confirm RLS is enabled on all tables
- [ ] Confirm service key has `BYPASSRLS` privilege (service role always does)

---

## Render Service Configuration

| Setting | Value |
|---------|-------|
| Service type | Web Service |
| Runtime | Docker |
| Instance type | **Standard (2 GB RAM)** minimum — free tier causes OOM with any BGE model |
| Health check path | `/health` |
| Health check timeout | 300s (model preload takes up to 120s) |
| Auto-deploy | Off (deploy manually after validation) |

**Persistent Disk** (strongly recommended):
- Mount path: `/app/.cache`
- Size: 2 GB
- This prevents re-downloading the 438 MB model on every cold start

---

## Pre-Deploy: Frontend

- [ ] `NEXT_PUBLIC_API_URL` set to the Render backend URL (without trailing `/api/v1`)
- [ ] `NEXT_PUBLIC_SUPABASE_URL` set
- [ ] `NEXT_PUBLIC_SUPABASE_ANON_KEY` set

---

## Post-Deploy Validation

Run these checks after the first deploy:

```bash
# 1. Root health check
curl https://your-backend.onrender.com/health

# Expected: status = "healthy", model.loaded = true (after ~60-120s)

# 2. CORS diagnostic
curl -H "Origin: https://hiremind-gilt.vercel.app" \
     https://your-backend.onrender.com/health/cors

# Expected: is_origin_allowed = true

# 3. Platform health stats (requires auth token)
curl -H "Authorization: Bearer <token>" \
     https://your-backend.onrender.com/api/v1/platform/health-stats
```

---

## Startup Log Checklist

After deploy, verify these log lines appear within 5 minutes:

```
[STARTUP_SUMMARY] pid=... rss=...MB model=BAAI/bge-base-en-v1.5 missing_vars=none
[MODEL_SERVICE] Starting background preload for model=BAAI/bge-base-en-v1.5
[MODEL_SERVICE] [MODEL_CACHE_MISS] name=BAAI/bge-base-en-v1.5 — downloading/loading
[MODEL_SERVICE] [MODEL_LOAD_COMPLETE] name=BAAI/bge-base-en-v1.5 elapsed=Xs
[MODEL_SERVICE] [MODEL_SINGLETON_CREATED] name=BAAI/bge-base-en-v1.5
[RECOVERY_SUMMARY] recovered=0 skipped=0 permanent_failures=0
```

**Red flags** (investigate immediately):
- `[STARTUP_ERROR] Required environment variable ... is not set`
- `[MODEL_SERVICE] [MODEL_LOAD_TIMEOUT]`
- `[MODEL_SERVICE] [MODEL_LOAD_FAILED]`
- `FAISS Failed to load`

---

## Rollback Plan

If deploy fails:
1. Revert to previous Render deploy from Dashboard → Deploys → previous commit → Rollback
2. All data is in Supabase — no data loss from backend rollback
3. Check `/health` on the rolled-back version to confirm it is healthy
