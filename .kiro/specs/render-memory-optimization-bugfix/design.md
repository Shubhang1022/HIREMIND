# Render Memory Optimization Bugfix Design

## Overview

This document describes the fix for application crashes during candidate file upload/indexing on Render's free tier (512MB RAM limit). The application currently reaches ~700MB peak memory during indexing, exceeding the available memory and causing the process to be OOM-killed mid-operation. This results in failed indexing operations with no progress saved and a generic "Candidates are in indexing" message that never completes.

The fix strategy involves aggressive memory optimization through: reducing batch sizes, implementing index streaming/chunking, stricter garbage collection, removing CUDA torch dependencies, and adding memory-aware circuit breakers to prevent OOM kills.

## Glossary

- **Bug_Condition (C)**: The condition that triggers the bug - when candidate file upload/indexing causes process memory to exceed available RAM (~512MB on Render free tier)
- **Property (P)**: The desired behavior - indexing completes successfully without OOM crashes, staying within 450-480MB peak memory
- **Preservation**: Existing functionality that must remain unchanged - semantic search accuracy, LLM scoring functionality, tenant isolation via RLS, and successful indexing on larger memory tiers
- **OOM Kill**: Out-of-Memory kill - when the Linux kernel terminates a process for exceeding available memory
- **RSS**: Resident Set Size - actual physical memory used by the process
- **Peak Memory**: Maximum RSS observed during an operation (currently ~700MB during indexing)
- **BGE-small**: BAAI/bge-small-en-v1.5 embedding model (~280MB loaded)
- **FAISS**: Facebook AI Similarity Search - vector index library (~35MB during indexing)
- **Batch Size**: Number of candidates processed in a single encoding operation (currently 16)
- **Lazy Loading**: Loading models on-demand rather than at startup (already implemented)

## Bug Details

### Bug Condition

The bug manifests when a user uploads a candidate file for processing. The backend begins indexing candidates (extracting embeddings and building FAISS indices), but the process memory grows beyond the 512MB available on Render's free tier. The Linux OOM killer terminates the process mid-operation, causing the indexing to fail without saving progress or returning a meaningful error to the user.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type CandidateUploadRequest
  OUTPUT: boolean
  
  RETURN input.fileUploaded == true
         AND indexingStarted(input.projectId) == true
         AND processMemoryRSS() > availableMemory()
         AND deploymentEnvironment == "RenderFreeTier"
         AND NOT indexingCompleted(input.projectId)
END FUNCTION
```

### Examples

- **User uploads 50 candidates on Render free tier**: Indexing starts → memory grows from 595MB baseline to ~730MB during encoding → OOM killer fires at ~680MB → process crashes → user sees indefinite "Candidates are in indexing" message
- **User uploads 100 candidates on Render free tier**: Indexing starts → memory grows from 595MB baseline to ~800MB during encoding → OOM killer fires at ~680MB → process crashes earlier in the batch
- **User uploads 10 candidates on Render free tier**: Indexing starts → memory grows from 595MB baseline to ~640MB during encoding → barely stays under limit → indexing completes (edge case that sometimes succeeds)
- **User uploads 1000 candidates on Railway (1GB RAM)**: Indexing starts → memory grows from 595MB baseline to ~730MB peak → stays well within 1GB limit → indexing completes successfully (preservation - this must still work)

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Semantic search accuracy must remain equivalent (same embedding model quality)
- LLM scoring functionality must continue to work with same accuracy
- Tenant isolation via Row-Level Security (RLS) must remain secure
- Successful indexing on larger memory tiers (Railway 1GB, Railway 2GB) must continue working
- API contract and response formats must remain unchanged
- Frontend behavior and user experience (except success rate) must remain unchanged

**Scope:**
All deployment environments that are NOT memory-constrained (Railway 1GB+, local development) should be completely unaffected by this fix. This includes:
- Development environment indexing performance
- Large-scale indexing jobs on production (Railway)
- Background job processing on non-free-tier deployments

## Hypothesized Root Cause

Based on the memory diagnostics and architecture analysis, the most likely causes are:

1. **Excessive Batch Size for Memory-Constrained Environment**: The current `EMBEDDING_BATCH_SIZE=16` is optimized for throughput on larger instances but causes memory spikes on Render free tier
   - Each batch loads 16 candidate text documents into memory
   - PyTorch creates intermediate tensors during encoding (~10-50MB per batch depending on text length)
   - FAISS index building requires additional memory (~35MB)

2. **Insufficient Garbage Collection During Indexing**: Temporary objects from batch processing are not aggressively freed
   - PyTorch allocator holds onto memory even after tensors are no longer referenced
   - Candidate text and intermediate numpy arrays linger in memory
   - FAISS index fragments accumulate during incremental builds

3. **CUDA Torch Wheel Memory Overhead**: The torch installation may include CUDA libraries (+350MB) unnecessary for CPU-only deployment
   - Dockerfile installs `torch==2.2.2 --index-url https://download.pytorch.org/whl/cpu`
   - But pip may still resolve to CUDA build depending on platform
   - TorchDiagnostics.md confirms CUDA build detection logging exists

4. **No Memory-Aware Circuit Breaker**: The existing 450MB threshold check in `run_analysis()` is not enforced during indexing
   - Check only applies to analysis endpoint, not indexing endpoint
   - No pre-flight memory check before starting expensive operations
   - No graceful degradation when approaching memory limits

## Correctness Properties

Property 1: Bug Condition - Indexing Completes Without OOM

_For any_ candidate upload where indexing is triggered on Render free tier (512MB RAM limit), the fixed indexing pipeline SHALL complete successfully without OOM crashes, maintaining peak memory usage below 480MB (with 32MB safety margin).

**Validates: Requirements 2.1 - Successful indexing on Render free tier, 2.2 - Peak memory < 512MB**

Property 2: Preservation - Semantic Search Accuracy and Functionality

_For any_ indexing operation on any deployment environment (free tier or larger), the fixed pipeline SHALL produce semantically equivalent search results as the original implementation, preserving embedding quality, LLM scoring functionality, tenant isolation, and all API contracts.

**Validates: Requirements 3.1 - Semantic search accuracy unchanged, 3.2 - LLM scoring preserved, 3.3 - Tenant isolation via RLS preserved, 3.4 - API contracts unchanged**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `backend/app/services/indexing_service.py` (or wherever indexing logic resides)

**Function**: Candidate indexing pipeline

**Specific Changes**:
1. **Reduce Batch Size for Render Free Tier**: Implement environment-aware batch sizing
   - Add `RENDER_FREE_TIER=true` environment detection
   - Reduce `EMBEDDING_BATCH_SIZE` from 16 to 4 when on free tier
   - Keep 16 for larger deployments (preservation)
   - Add logging: `[BATCH_SIZE_ADAPTIVE] tier=free batch_size=4`

2. **Implement Streaming FAISS Index Building**: Build indices incrementally with memory cleanup between batches
   - Process candidates in micro-batches (4 at a time on free tier)
   - Build FAISS index incrementally via `add()` instead of bulk construction
   - Call `gc.collect()` and `torch.cuda.empty_cache()` (if available) after each micro-batch
   - Add memory logging after each batch: `[BATCH_COMPLETE] batch=N/M rss=XMB`

3. **Add Pre-Flight Memory Check**: Reject indexing requests when memory is already high
   - Check `get_memory_mb()` before starting indexing
   - If RSS > 400MB, return 503 Service Unavailable with retry-after header
   - Add logging: `[INDEXING_REJECTED] reason=high_memory rss=XMB threshold=400MB`

4. **Force CPU-Only Torch Build**: Ensure Dockerfile pins CPU-only torch without CUDA
   - Change `pip install torch==2.2.2 --index-url https://download.pytorch.org/whl/cpu` to explicit CPU wheel
   - Use: `pip install torch==2.2.2+cpu --extra-index-url https://download.pytorch.org/whl/cpu`
   - Verify in build: `[MODEL_BAKE] PyTorch CPU check: cuda_available=False`

5. **Aggressive Memory Management During Indexing**: Force garbage collection at strategic points
   - Call `gc.collect()` after each candidate batch encoding
   - Delete numpy arrays and text immediately after encoding: `del candidate_texts; del embeddings`
   - Use context managers to ensure cleanup: `with BatchContext() as batch: ...`

6. **Add Memory Monitoring and Auto-Throttling**: Dynamically reduce batch size if memory approaches limit
   - Check RSS before each batch
   - If RSS > 430MB, reduce batch size to 1
   - If RSS > 460MB, pause indexing and run full GC + retry once
   - If RSS still > 460MB after GC, fail gracefully with 507 Insufficient Storage

**File**: `backend/Dockerfile`

**Specific Changes**:
7. **Pin CPU-Only Torch**: Ensure CUDA libraries are never included
   - Replace: `pip install --no-cache-dir torch==2.2.2 --index-url https://download.pytorch.org/whl/cpu`
   - With: `pip install --no-cache-dir torch==2.2.2+cpu --extra-index-url https://download.pytorch.org/whl/cpu`

**File**: `backend/app/core/config.py`

**Specific Changes**:
8. **Add Render-Specific Configuration**: Environment detection and adaptive settings
   - Add `RENDER_FREE_TIER` boolean setting (detect from `RENDER=true` and `RENDER_INSTANCE_TYPE` env vars)
   - Add `ADAPTIVE_BATCH_SIZE` logic: 4 for free tier, 16 for others
   - Add `MEMORY_CIRCUIT_BREAKER_THRESHOLD_MB=400` setting

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code (confirm OOM crashes occur), then verify the fix works correctly and preserves existing behavior on larger instances.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm that indexing crashes on Render free tier. Confirm the root cause analysis.

**Test Plan**: Deploy the UNFIXED code to a Render free tier instance, upload varying sizes of candidate files, and observe the crash behavior and memory patterns. Monitor RSS throughout the indexing process to identify the exact point of OOM.

**Test Cases**:
1. **50 Candidate Upload Test**: Upload 50 candidates → expect crash at ~680MB RSS (will fail on unfixed code)
2. **100 Candidate Upload Test**: Upload 100 candidates → expect crash earlier in batch processing (will fail on unfixed code)
3. **10 Candidate Upload Test**: Upload 10 candidates → may succeed or fail depending on text length (edge case)
4. **Memory Profiling Test**: Instrument unfixed code with RSS logging every 1s during indexing → identify peak memory and crash point

**Expected Counterexamples**:
- Process OOM-killed with exit code 137 (SIGKILL)
- Logs show: `[MEMORY_DIAGNOSTICS] [STAGE_START] stage=encode_batch ... RSS=680.0MB` followed by process termination
- No `[INDEXING_COMPLETE]` log entry
- Possible root causes confirmed: batch size too large, insufficient GC, CUDA torch overhead

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds (candidate uploads on Render free tier), the fixed function produces the expected behavior (successful indexing without OOM).

**Pseudocode:**
```
FOR ALL candidateUpload WHERE isBugCondition(candidateUpload) DO
  result := indexCandidates_fixed(candidateUpload)
  ASSERT result.status == "success"
  ASSERT result.peakMemoryMB < 480
  ASSERT result.searchResultsValid == true
END FOR
```

**Test Plan**: Deploy the FIXED code to Render free tier, run the same test cases, and verify:
- Indexing completes successfully
- Peak RSS stays below 480MB
- Search results are semantically equivalent to unfixed code on larger instances
- No OOM kills occur

**Test Cases**:
1. **50 Candidate Upload Test (Fixed)**: Upload 50 candidates → indexing completes → peak RSS < 480MB
2. **100 Candidate Upload Test (Fixed)**: Upload 100 candidates → indexing completes → peak RSS < 480MB
3. **200 Candidate Upload Test (Fixed)**: Upload 200 candidates → indexing completes → peak RSS < 480MB
4. **Memory Profile Comparison**: Compare RSS timeline fixed vs unfixed → demonstrate memory reduction

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold (deployments on larger instances, development environment), the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL candidateUpload WHERE NOT isBugCondition(candidateUpload) DO
  ASSERT indexCandidates_original(candidateUpload) = indexCandidates_fixed(candidateUpload)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across the input domain (varying candidate counts, text lengths, project configurations)
- It catches edge cases that manual unit tests might miss (unicode text, empty fields, large documents)
- It provides strong guarantees that behavior is unchanged for all non-Render-free-tier inputs

**Test Plan**: Observe behavior on UNFIXED code first on Railway (1GB RAM) for various candidate uploads, then write property-based tests capturing that behavior. Run tests on FIXED code and verify identical results.

**Test Cases**:
1. **Railway 1GB Preservation**: Deploy fixed code to Railway 1GB → upload 1000 candidates → verify indexing completes at same speed and accuracy as unfixed code
2. **Search Accuracy Preservation**: For N candidate sets, compare search results between fixed and unfixed code → verify cosine similarity of top-K results is identical
3. **LLM Scoring Preservation**: Run scoring workflow on both versions → verify scores are identical (within floating-point precision)
4. **Tenant Isolation Preservation**: Verify RLS policies still enforce per-tenant data access in fixed code

### Unit Tests

- Test adaptive batch size selection based on `RENDER_FREE_TIER` flag (4 vs 16)
- Test pre-flight memory check rejection logic (RSS > 400MB → 503)
- Test incremental FAISS index building with micro-batches
- Test garbage collection is called after each batch
- Test memory monitoring and auto-throttling (batch size reduction when RSS > 430MB)
- Test graceful failure when memory still high after GC (507 response)

### Property-Based Tests

- Generate random candidate datasets (varying count, text length, field completeness) → verify all complete successfully on Render free tier with fixed code
- Generate random project configurations → verify indexing behavior is identical between fixed and unfixed code on Railway 1GB
- Generate random search queries → verify search results are semantically equivalent (cosine similarity of top-K within 0.01) between fixed and unfixed code

### Integration Tests

- Test full candidate upload → indexing → search flow on Render free tier with fixed code (50, 100, 200 candidates)
- Test multiple concurrent indexing jobs on Render free tier → verify memory isolation and no OOM
- Test indexing on Railway 1GB → verify performance and accuracy unchanged from unfixed code
- Test that memory diagnostics logs are emitted correctly throughout indexing process
