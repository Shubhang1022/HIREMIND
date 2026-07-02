"""Hardcoded enriched JD text for embedding-based semantic similarity.

This module is the single source of truth for the Job Description text used
in Phase 1 (JD embedding pre-computation) and Phase 2 (cosine similarity
scoring).  It is deliberately keyword-enriched and paraphrased to maximize
coverage of the semantic space relevant to the Senior AI Engineer role at
Redrob AI.

See docs/RankingLogic.md §3.2.1 for the design rationale.
"""

JD_TEXT: str = (
    "Senior AI Engineer founding team role at Redrob AI, Pune or Noida India hybrid, "
    "5 to 9 years experience with sweet spot 6 to 8 years. "
    "Requires hands-on production experience with embedding-based retrieval systems using "
    "sentence-transformers, BGE, E5, OpenAI embeddings; embedding drift detection, "
    "index refresh pipelines, retrieval quality regression testing. "
    "Must have production vector database experience with hybrid search — "
    "Pinecone, Weaviate, Qdrant, Milvus, OpenSearch, Elasticsearch, FAISS, ANN indexing. "
    "Strong Python proficiency: clean code, testing, type hints, async, production-grade pipelines. "
    "Hands-on evaluation framework design for ranking and search systems: "
    "NDCG, MRR, MAP, Precision@K, learning-to-rank, offline evaluation corpora, "
    "online A/B test design and statistical interpretation. "
    "Nice to have: LLM fine-tuning with LoRA, QLoRA, PEFT; "
    "learning-to-rank models including XGBoost RankNet LambdaMART pairwise listwise; "
    "ML infrastructure — Kubernetes, Docker, MLOps, Ray, Triton, ONNX, quantization; "
    "HR-tech or recruiting marketplace product experience; "
    "distributed systems and large-scale inference optimization; "
    "open source AI/ML contributions. "
    "Product company experience is essential — not consulting or services background. "
    "Demonstrated end-to-end ownership: deployed ranking, search, or recommendation system "
    "serving real users at scale with latency SLAs, throughput requirements, A/B testing. "
    "Salary range 25 to 55 LPA. "
    "Hard disqualifiers: consulting-only career (TCS, Wipro, Infosys, Accenture), "
    "LLM hype experience under 12 months with zero pre-LLM production ML, "
    "no evidence of any production deployment whatsoever, "
    "entire career in pure academic or research settings with no industry deployment."
)
