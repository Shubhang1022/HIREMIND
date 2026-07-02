from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.features.cache import FeatureCache
from src.intelligence.embeddings import BGELargeEmbeddingLayer
from src.intelligence.explainability import ExplainabilityEngine
from src.intelligence.interview_copilot import InterviewCopilot
from src.intelligence.ranking import AIRankingEngine
from src.intelligence.retrieval import SimilarityRetriever
from src.intelligence.types import Explainability, InterviewPlan, JobUnderstanding, RetrievalResult
from src.intelligence.understanding import JobUnderstandingEngine


@dataclass
class IntelligenceLayer:
    """End-to-end intelligence facade for backend/CLI integration."""

    cache_dir: str
    model_name: str = "BAAI/bge-large-en-v1.5"

    def __post_init__(self) -> None:
        self.cache = FeatureCache(self.cache_dir)
        self.embedder = BGELargeEmbeddingLayer(model_name=self.model_name)
        self.job_engine = JobUnderstandingEngine()
        self.retriever = SimilarityRetriever(cache_dir=self.cache_dir)
        self.ranker = AIRankingEngine()
        self.explainer = ExplainabilityEngine()
        self.copilot = InterviewCopilot()

    def embed_job(self, jd_text: str) -> np.ndarray:
        return self.embedder.embed_job(jd_text)

    def understand_job(self, jd_text: str) -> JobUnderstanding:
        return self.job_engine.extract(jd_text)

    def retrieve_top_candidates(self, jd_text: str, k: int = 50) -> tuple[JobUnderstanding, np.ndarray, list[RetrievalResult]]:
        job_u = self.understand_job(jd_text)
        job_emb = self.embed_job(jd_text)
        top = self.retriever.top_k(job_emb, k=k)
        return job_u, job_emb, top

    def explain_and_questions(
        self,
        *,
        candidate_id: str,
        job: JobUnderstanding,
        semantic_similarity: float,
        features: dict,
    ) -> tuple[float, Explainability, InterviewPlan]:
        signals = self.ranker.score_candidate(
            features=features, job=job, semantic_similarity=semantic_similarity
        )
        final = self.ranker.final_score(signals)
        exp = self.explainer.explain(
            candidate_id=candidate_id, features=features, job=job, signals=signals
        )
        plan = self.copilot.generate(
            candidate_id=candidate_id,
            features=features,
            job=job,
            missing_skills=exp.missing_skills,
            risks=exp.risks,
        )
        return final, exp, plan

