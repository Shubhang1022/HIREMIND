# Implementation Plan

## Overview
This plan implements the memory optimization fix to reduce peak memory from 700MB to under 480MB on Render free tier (512MB RAM limit). The fix uses adaptive batch sizing, streaming FAISS index building, aggressive garbage collection, CPU-only torch pinning, and memory-aware circuit breakers.

---

## Tasks

- [ ] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - OOM Crashes During Indexing on Render Free Tier
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate OOM crashes occur during candidate indexing on Render free tier
  - **Scoped PBT Approach**: Scope the property to concrete failing cases - 50-100 candidate uploads on Render free tier (512MB RAM)
  - Test implementation details from Bug Condition in design:
    - Deploy UNFIXED code to Render free tier instance
    - Upload 50 candidates via API endpoint
    - Monitor process RSS memory throughout indexing
    - Assert that process does NOT get OOM-killed (exit code 137)
    - Assert that peak RSS memory stays below 480MB
    - Assert that indexing completes successfully
  - The test assertions should match the Expected Behavior Properties from design:
    - `result.status == "success"`
    - `result.peakMemoryMB < 480`
    - `result.searchResultsValid == true`
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (process crashes with OOM kill, exit code 137, RSS exceeds 512MB)
  - Document counterexamples found:
    - Exact RSS value at crash point (expected ~680-700MB)
    - Number of candidates processed before crash
    - Batch number where crash occurred
    - Memory growth pattern from logs
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 2.1, 2.2_

- [ ] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Non-Memory-Constrained Environments Unchanged
  - **IMPORTANT**: Follow observation-first methodology
  - Observe behavior on UNFIXED code for non-buggy inputs (Railway 1GB+ instances, local development)
  - Write property-based tests capturing observed behavior patterns from Preservation Requirements:
    - Test 1: Indexing 1000 candidates on Railway 1GB completes successfully
    - Test 2: Search results are semantically equivalent (cosine similarity within 0.01)
    - Test 3: LLM scoring produces identical scores (within floating-point precision)
    - Test 4: Tenant isolation via RLS policies are enforced correctly
    - Test 5: API response formats and contracts remain unchanged
    - Test 6: Indexing performance on larger tiers is not degraded
  - Property-based testing generates many test cases for stronger guarantees:
    - Generate random candidate datasets (varying count, text length, field completeness)
    - Generate random project configurations
    - Generate random search queries
  - Run tests on UNFIXED code deployed to Railway 1GB
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [ ] 3. Fix for memory optimization to enable Render free tier indexing

  - [ ] 3.1 Add environment detection and adaptive configuration
    - Add `RENDER_FREE_TIER` boolean detection in `backend/app/core/config.py`
    - Detect from environment variables: `RENDER=true` and check `RENDER_INSTANCE_TYPE` or memory limits
    - Add `ADAPTIVE_BATCH_SIZE` configuration: 4 for free tier, 16 for larger deployments
    - Add `MEMORY_CIRCUIT_BREAKER_THRESHOLD_MB=400` setting
    - Add logging: `[CONFIG_ADAPTIVE] tier=free batch_size=4 threshold_mb=400`
    - _Bug_Condition: isBugCondition(input) where input.fileUploaded == true AND indexingStarted == true AND processMemoryRSS() > availableMemory() AND deploymentEnvironment == "RenderFreeTier"_
    - _Expected_Behavior: Adaptive batch sizing reduces memory spikes to stay within 480MB peak_
    - _Preservation: Larger deployments (Railway 1GB+) continue using batch_size=16 for optimal performance_
    - _Requirements: 2.1, 2.2, 3.4_

  - [ ] 3.2 Implement pre-flight memory check circuit breaker
    - Add memory check at start of indexing endpoint before processing begins
    - Get current RSS using `get_memory_mb()` helper
    - If RSS > 400MB, return HTTP 503 Service Unavailable with Retry-After header
    - Add logging: `[INDEXING_REJECTED] reason=high_memory rss=XMB threshold=400MB`
    - This prevents starting expensive operations when memory is already constrained
    - _Bug_Condition: Prevents indexing from starting when RSS is already high, avoiding guaranteed OOM_
    - _Expected_Behavior: Graceful rejection with retry guidance instead of OOM crash_
    - _Preservation: Does not affect normal operation when memory is healthy_
    - _Requirements: 2.1, 2.2_

  - [ ] 3.3 Implement streaming FAISS index building with micro-batches
    - Modify indexing pipeline in `backend/app/services/indexing_service.py`
    - Process candidates in micro-batches based on `ADAPTIVE_BATCH_SIZE` (4 on free tier, 16 on larger)
    - Build FAISS index incrementally using `add()` method instead of bulk construction
    - After each micro-batch:
      - Call `gc.collect()` to force garbage collection
      - Call `torch.cuda.empty_cache()` if CUDA is available
      - Delete temporary numpy arrays: `del candidate_texts; del embeddings`
      - Log memory status: `[BATCH_COMPLETE] batch=N/M rss=XMB`
    - Use context managers to ensure cleanup: `with BatchContext() as batch: ...`
    - _Bug_Condition: Reduces peak memory by processing fewer candidates simultaneously and aggressively freeing memory_
    - _Expected_Behavior: Peak RSS stays below 480MB during indexing_
    - _Preservation: Semantic search accuracy unchanged (same model, same embeddings)_
    - _Requirements: 2.1, 2.2, 3.1_

  - [ ] 3.4 Add memory monitoring and auto-throttling during indexing
    - Before each batch, check current RSS using `get_memory_mb()`
    - If RSS > 430MB, reduce batch size to 1 (emergency throttling)
    - If RSS > 460MB, pause indexing and run full GC + retry once
    - If RSS still > 460MB after GC, fail gracefully with HTTP 507 Insufficient Storage
    - Add logging at each threshold:
      - `[MEMORY_WARNING] rss=430MB action=reduce_batch_size`
      - `[MEMORY_CRITICAL] rss=460MB action=pause_and_gc`
      - `[MEMORY_FAILURE] rss=460MB action=abort status=507`
    - _Bug_Condition: Prevents OOM by dynamically responding to memory pressure_
    - _Expected_Behavior: Graceful degradation instead of OOM crash_
    - _Preservation: Larger instances never hit these thresholds, no impact on performance_
    - _Requirements: 2.1, 2.2_

  - [ ] 3.5 Pin CPU-only PyTorch build in Dockerfile
    - Modify `backend/Dockerfile` to explicitly install CPU-only torch wheel
    - Replace: `pip install --no-cache-dir torch==2.2.2 --index-url https://download.pytorch.org/whl/cpu`
    - With: `pip install --no-cache-dir torch==2.2.2+cpu --extra-index-url https://download.pytorch.org/whl/cpu`
    - This ensures CUDA libraries (~350MB) are never included in the image
    - Add build verification: `RUN python -c "import torch; assert not torch.cuda.is_available(), 'CUDA should not be available'"`
    - Add logging in application startup: `[MODEL_BAKE] PyTorch CPU check: cuda_available=False`
    - _Bug_Condition: Eliminates unnecessary CUDA library memory overhead_
    - _Expected_Behavior: Reduces baseline memory footprint by ~100-200MB_
    - _Preservation: CPU-only inference is already the case, no functional change_
    - _Requirements: 2.2_

  - [ ] 3.6 Add comprehensive memory diagnostics logging
    - Add RSS logging at each stage of indexing pipeline:
      - `[STAGE_START] stage=pre_flight rss=XMB`
      - `[STAGE_START] stage=load_candidates rss=XMB`
      - `[STAGE_START] stage=encode_batch batch=N/M rss=XMB`
      - `[STAGE_COMPLETE] stage=encode_batch batch=N/M rss=XMB`
      - `[STAGE_START] stage=build_index rss=XMB`
      - `[INDEXING_COMPLETE] total_batches=M peak_rss=XMB`
    - Log memory delta between stages: `[MEMORY_DELTA] stage=encode_batch delta=+25MB`
    - This enables debugging and monitoring of memory usage patterns
    - _Bug_Condition: Provides visibility into memory growth patterns for debugging_
    - _Expected_Behavior: Clear diagnostic trail for successful and failed indexing attempts_
    - _Preservation: Logging has no functional impact_
    - _Requirements: 2.1, 2.2_

  - [ ] 3.7 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Indexing Completes Without OOM on Render Free Tier
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected behavior
    - When this test passes, it confirms the expected behavior is satisfied
    - Deploy FIXED code to Render free tier instance
    - Run bug condition exploration test from step 1:
      - Upload 50 candidates via API endpoint
      - Monitor process RSS memory throughout indexing
      - Verify process does NOT get OOM-killed
      - Verify peak RSS memory stays below 480MB
      - Verify indexing completes successfully
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - Compare memory profiles:
      - Unfixed: RSS peaked at ~680-700MB → OOM crash
      - Fixed: RSS peaks at <480MB → indexing completes
    - Document results:
      - Peak RSS on fixed code
      - Number of batches processed
      - Total indexing time
      - Memory growth pattern from logs
    - _Requirements: 2.1, 2.2_

  - [ ] 3.8 Verify preservation tests still pass
    - **Property 2: Preservation** - Non-Memory-Constrained Environments Unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Deploy FIXED code to Railway 1GB instance
    - Run preservation property tests from step 2:
      - Test 1: Index 1000 candidates → verify completes successfully
      - Test 2: Compare search results → verify cosine similarity within 0.01
      - Test 3: Run LLM scoring → verify scores identical (within floating-point precision)
      - Test 4: Verify RLS policies enforce tenant isolation
      - Test 5: Verify API response formats unchanged
      - Test 6: Compare indexing performance metrics → verify no degradation
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Compare behavior between unfixed and fixed code on Railway 1GB:
      - Indexing completion time (should be similar)
      - Search result quality (should be identical)
      - LLM scores (should be identical)
      - API contracts (should be unchanged)
    - Run property-based tests with generated datasets:
      - Random candidate counts (10 to 2000)
      - Random text lengths (short to long resumes)
      - Random field completeness (all fields vs sparse)
      - Verify all behave identically between fixed and unfixed code
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [ ] 4. Checkpoint - Ensure all tests pass
  - Verify bug condition test passes on Render free tier (50+ candidates index successfully, RSS < 480MB)
  - Verify preservation tests pass on Railway 1GB (search accuracy, LLM scoring, tenant isolation, API contracts all unchanged)
  - Review memory diagnostic logs for any anomalies or unexpected patterns
  - Confirm no OOM crashes occur during any test scenarios
  - If any issues arise, investigate and resolve before considering the fix complete
  - Ask the user if questions arise or clarifications needed

---

## Notes

### Bug Condition Methodology

This task list follows the bug condition methodology:
- **C(X)**: Bug Condition - indexing on Render free tier causes OOM (RSS > 512MB)
- **P(result)**: Property - indexing completes successfully with RSS < 480MB
- **¬C(X)**: Non-buggy inputs - indexing on Railway 1GB+ (should be preserved)
- **F**: Original (unfixed) function - current indexing pipeline
- **F'**: Fixed function - optimized indexing pipeline

### Key Implementation Constraints

1. **Adaptive Configuration**: Batch size must be 4 on Render free tier, 16 on larger instances
2. **Streaming Processing**: FAISS index built incrementally with aggressive GC between batches
3. **Circuit Breakers**: Pre-flight check (RSS > 400MB → reject), auto-throttling (RSS > 430MB → reduce batch size)
4. **CPU-Only Build**: Dockerfile must pin torch==2.2.2+cpu explicitly
5. **Comprehensive Logging**: Memory diagnostics at every stage for debugging

### Testing Constraints

1. **Exploration Test**: MUST be written and run BEFORE implementing the fix (will fail on unfixed code)
2. **Preservation Tests**: MUST be written and run on unfixed code BEFORE implementing the fix (will pass on unfixed code)
3. **Property-Based Testing**: Recommended for preservation to catch edge cases
4. **Memory Monitoring**: Use RSS logging throughout all test runs to validate memory behavior

### Requirements Traceability

- **Requirement 2.1**: Successful indexing on Render free tier (512MB RAM limit)
  - Validated by: Property 1 (Bug Condition test)
  - Implemented by: Tasks 3.1, 3.2, 3.3, 3.4, 3.6

- **Requirement 2.2**: Peak memory < 512MB (target 480MB with safety margin)
  - Validated by: Property 1 (Bug Condition test)
  - Implemented by: Tasks 3.1, 3.3, 3.4, 3.5, 3.6

- **Requirement 3.1**: Semantic search accuracy unchanged
  - Validated by: Property 2 (Preservation test - search results comparison)
  - Implemented by: Tasks 3.3 (same model, same embeddings)

- **Requirement 3.2**: LLM scoring functionality preserved
  - Validated by: Property 2 (Preservation test - LLM score comparison)
  - Implemented by: No changes to scoring logic

- **Requirement 3.3**: Tenant isolation via RLS preserved
  - Validated by: Property 2 (Preservation test - RLS policy enforcement)
  - Implemented by: No changes to database access logic

- **Requirement 3.4**: API contracts unchanged
  - Validated by: Property 2 (Preservation test - API response format validation)
  - Implemented by: Tasks 3.1, 3.2, 3.4 (internal changes only, external API unchanged)
