# Bugfix Requirements Document

## Introduction

The HireMind AI resume analyzer application currently exceeds the 512MB RAM limit on Render's free tier deployment, causing the application to crash or behave incorrectly. According to ModelLoadingAudit.md, the application's Resident Set Size (RSS) reaches approximately 545MB after loading the BGE-small embedding model and peaks at 700MB during active indexing of candidate datasets. This memory footprint exceeds Render's constraint by at least 33MB in steady state and up to 188MB during peak operations, causing deployment failures and service interruptions.

The application uses machine learning models (SentenceTransformer embeddings), in-memory FAISS vector indices, and background job processing, all contributing to memory consumption. The goal is to reduce memory utilization to fit comfortably within the 512MB constraint while maintaining core resume analysis functionality.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the application loads the BGE-small embedding model at startup THEN the system consumes approximately 545MB RSS memory

1.2 WHEN the application performs background indexing operations with embedding generation THEN the system memory usage peaks at approximately 700MB RSS

1.3 WHEN memory usage exceeds 512MB on Render free tier THEN the deployment breaks and the model fails to operate correctly

1.4 WHEN multiple components (PyTorch, FAISS index, embedding model, tokenizer, application code) are loaded simultaneously THEN the cumulative memory footprint exceeds the deployment constraint

### Expected Behavior (Correct)

2.1 WHEN the application loads the embedding model THEN the system SHALL consume no more than 400MB RSS to leave headroom for peak operations

2.2 WHEN the application performs background indexing operations THEN the system memory usage SHALL NOT exceed 512MB RSS under any circumstances

2.3 WHEN deployed on Render free tier THEN the application SHALL operate reliably within the 512MB memory limit without crashes or failures

2.4 WHEN optimizing memory usage THEN the system SHALL maintain core functionality including resume analysis, candidate ranking, and embedding-based search

### Unchanged Behavior (Regression Prevention)

3.1 WHEN the application performs semantic resume search THEN the system SHALL CONTINUE TO return accurate vector similarity results

3.2 WHEN users upload candidate datasets THEN the system SHALL CONTINUE TO extract and index resume content correctly

3.3 WHEN the LLM scoring endpoint is called THEN the system SHALL CONTINUE TO generate candidate strengths, weaknesses, and recommendations

3.4 WHEN multiple users access the system concurrently THEN the system SHALL CONTINUE TO maintain tenant isolation via Row-Level Security

3.5 WHEN the application performs metadata-based filtering THEN the system SHALL CONTINUE TO pre-filter candidates by experience and skills

3.6 WHEN embedding dimensions are validated THEN the system SHALL CONTINUE TO ensure consistency between stored and generated embeddings

## Bug Condition Derivation

### Bug Condition Function

```pascal
FUNCTION isBugCondition(deployment_context)
  INPUT: deployment_context containing {
    peak_memory_mb: float,
    memory_limit_mb: float,
    deployment_platform: string
  }
  OUTPUT: boolean
  
  // Bug occurs when peak memory exceeds the deployment limit
  RETURN (deployment_context.peak_memory_mb > deployment_context.memory_limit_mb) 
         AND (deployment_context.deployment_platform = "Render Free Tier")
END FUNCTION
```

**Concrete Instance:**
- Current peak memory: 700MB (during indexing)
- Memory limit: 512MB
- Platform: Render Free Tier
- Result: `isBugCondition(current_deployment) = TRUE`

### Property Specification (Fix Checking)

```pascal
// Property: Memory constraint satisfaction after optimization
FOR ALL deployment_contexts WHERE isBugCondition(deployment_context) DO
  optimized_system ← apply_memory_optimizations(system)
  peak_memory ← measure_peak_memory(optimized_system)
  
  ASSERT peak_memory <= deployment_context.memory_limit_mb
  ASSERT maintains_core_functionality(optimized_system)
END FOR
```

**Expected Outcome:**
- Optimized peak memory: ≤ 512MB (target: ~450-480MB to provide safety margin)
- Core functionality preserved: resume analysis, vector search, LLM scoring, tenant isolation

### Preservation Property (Regression Prevention)

```pascal
// Property: Functional equivalence for non-memory-constrained deployments
FOR ALL operations WHERE NOT memory_constrained(operation) DO
  original_result ← execute_operation(original_system, operation)
  optimized_result ← execute_operation(optimized_system, operation)
  
  ASSERT functional_equivalence(original_result, optimized_result)
END FOR
```

**Preservation Goals:**
- Semantic search accuracy maintained
- Candidate ranking quality unchanged
- LLM evaluation outputs consistent
- Database operations and RLS policies unaffected
- API response formats and contracts preserved

## Potential Optimization Strategies

The following strategies are candidates for reducing memory usage (implementation details belong in design.md):

1. **Model Loading Strategy**: Switch from preloading at startup to lazy loading on first request
2. **Model Selection**: Evaluate smaller embedding models (current: BGE-small 384-dim)
3. **FAISS Index Management**: Implement index unloading after use or on-demand loading
4. **Batch Size Reduction**: Reduce embedding batch sizes during indexing
5. **Garbage Collection**: Aggressive memory cleanup after heavy operations
6. **Process Worker Configuration**: Ensure single-worker deployment (avoid per-worker memory duplication)
7. **Thread Pool Limits**: Restrict parallelism to reduce concurrent memory allocation
8. **Dependency Optimization**: Remove unused ML dependencies from deployment

These strategies will be evaluated and prioritized in the design phase based on memory impact vs. functionality tradeoffs.
