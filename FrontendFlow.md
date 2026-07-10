# FrontendFlow.md

**Generated:** 2026-07-09  
**File:** `frontend/src/app/(dashboard)/projects/[id]/page.tsx`

---

## Correct Upload → Index → Analysis Flow

```
User uploads candidates
        │
        ▼
POST /upload  ──────────────────▶  Backend saves raw file
        │                                    │
        │ Response: { status: "queued" }      ▼
        │                          Background job created
        │                          process_candidate_upload_task starts
        │
        ▼
Frontend shows:
  "File accepted — indexing started.
   Analysis will be available once indexing completes."
        │
        ▼
SSE listener activates (embedding_status ∈ {queued, processing, embedding, indexing})
        │
        ├─ Progress bar updates every 2s
        ├─ Stage label shown ("Embedding batch 3/12...")
        └─ RAM usage shown
        │
        ▼
SSE terminal event: status = "completed"
        │
        ▼
load() refreshes project (embedding_status = "completed")
        │
        ▼
canRunAnalysis = true  →  "Run AI Analysis" button enabled
        │
        ▼
User clicks Run AI Analysis
        │
        ▼
POST /analyze  ──────────────────▶  Full pipeline executes
        │
        ▼
Redirect to /ranking page
```

---

## Upload Response Handling

**Before fix (wrong):**
```typescript
toast.success(`${file.name} uploaded successfully`);  // ← implies complete
```

**After fix (correct):**
```typescript
toast.info(`${file.name} accepted — indexing started. Analysis will be available once indexing completes.`);
```

---

## SSE Listener Coverage

The SSE listener now activates for all active indexing states:

```typescript
const activeStatuses = ['queued', 'processing', 'embedding', 'indexing'];
if (!project || !activeStatuses.includes(project.embedding_status ?? '')) {
  setWorkerStatus(null);
  return;
}
```

Previously only monitored `queued` and `processing` — meaning the UI went dark during the embedding and indexing stages.

---

## Indexing Failed — Retry Flow

**No re-upload required.**

When `embedding_status === 'failed'`:
1. Candidates tab shows red alert banner with "Retry Indexing" button
2. Results tab shows red alert banner with "Retry Indexing" button
3. Neither shows "please re-upload"

Clicking "Retry Indexing" calls `POST /retry-indexing`. The backend reuses the stored candidate file.

After retry starts:
- `load()` refreshes project
- SSE listener activates again
- Progress bar shows new run

---

## `canRunAnalysis` Logic

```typescript
const isEmbeddingReady = project.embedding_status === 'ready' || project.embedding_status === 'completed';
const canRunAnalysis = project.candidate_count > 0 && !!selectedJobId && isEmbeddingReady;
```

The "Run AI Analysis" button is **only enabled** when indexing is fully complete. It remains disabled (with contextual tooltip) while:
- No candidates uploaded
- No job description selected
- Indexing in progress
- Indexing failed

---

## Analysis Error Handling

If `POST /analyze` returns 409 with `code: "INDEXING_FAILED"`, the error message now says:

```
"Candidate indexing failed. Use the retry endpoint to restart indexing — no re-upload required."
```

(Previously said "Please re-upload candidate files to retry".)

---

## Indexing Status Banners

| Location | Condition | Content |
|---|---|---|
| Candidates tab | `embedding_status ∈ {queued, processing, embedding, indexing}` | Amber spinner + progress bar + stage label |
| Candidates tab | `embedding_status === 'failed'` | Red alert + "Retry Indexing" button |
| Candidates tab | `embedding_status === 'completed'` | Green "X candidates uploaded — ready for analysis" |
| Results tab | `embedding_status ∈ active` | Amber spinner + progress bar |
| Results tab | `embedding_status === 'failed'` | Red alert + "Retry Indexing" button |
