from __future__ import annotations

from dataclasses import dataclass

from src.intelligence.types import Explainability, JobUnderstanding, RankingSignals


def _fmt_pct(x: float) -> str:
    return f"{int(round(max(0.0, min(1.0, x)) * 100))}%"


@dataclass
class ExplainabilityEngine:
    """Grounded explanations (no LLM calls).

    Uses job understanding + candidate structured features + ranking signals.
    """

    def explain(
        self,
        *,
        candidate_id: str,
        features: dict,
        job: JobUnderstanding,
        signals: RankingSignals,
    ) -> Explainability:
        why_selected: list[str] = []
        why_not: list[str] = []
        missing: list[str] = []
        risks: list[str] = []

        # Why selected
        if signals.semantic_fit >= 0.75:
            why_selected.append(f"Strong semantic match to role ({_fmt_pct(signals.semantic_fit)})")
        elif signals.semantic_fit >= 0.55:
            why_selected.append(f"Solid semantic alignment ({_fmt_pct(signals.semantic_fit)})")
        if features.get("production_evidence_score", 0.0) >= 0.5:
            why_selected.append("Clear production ML evidence in experience descriptions")
        if features.get("has_vector_db"):
            why_selected.append("Vector DB / ANN search exposure (Pinecone/Weaviate/Qdrant/FAISS)")
        if features.get("has_embedding_retrieval"):
            why_selected.append("Embeddings / dense retrieval exposure (BGE/E5/sentence-transformers)")
        if features.get("has_evaluation_framework"):
            why_selected.append("Ranking/retrieval evaluation experience (NDCG/MRR/MAP/LTR)")
        if features.get("has_python_advanced"):
            why_selected.append("Advanced Python proficiency verified in skills")
        if signals.leadership >= 0.6:
            why_selected.append("Leadership/ownership signals in prior roles")
        if features.get("open_to_work"):
            why_selected.append("Actively open to work")
        if int(features.get("notice_period_days", 999) or 999) <= 30:
            why_selected.append("Short notice period (≤30 days)")

        # Missing skills vs required
        req = [s.lower() for s in (job.required_skills or [])]
        # Map structured booleans to required clusters
        has_embedding = bool(features.get("has_embedding_retrieval"))
        has_vdb = bool(features.get("has_vector_db"))
        has_eval = bool(features.get("has_evaluation_framework"))
        has_python = bool(features.get("has_python_advanced")) or ("python" in (str(features.get("candidate_text", "")).lower()))

        for r in req:
            if "embedding" in r and not has_embedding:
                missing.append("Embeddings / dense retrieval (e.g., BGE/E5/sentence-transformers)")
            if "vector" in r and not has_vdb:
                missing.append("Vector database / ANN search (Pinecone/Weaviate/Qdrant/Milvus/FAISS)")
            if "evaluation" in r and not has_eval:
                missing.append("Ranking/retrieval evaluation (NDCG/MRR/MAP, offline→online)")
            if r == "python" and not has_python:
                missing.append("Strong Python")

        # Why not selected: only include if low signals
        if signals.experience_fit < 0.5:
            why_not.append("Experience fit below target band (years/product mix/production evidence)")
        if signals.hiring_readiness < 0.5:
            why_not.append("Hiring readiness constraints (open-to-work/recency/notice period)")
        if signals.career_stability < 0.5:
            why_not.append("Career stability concerns (short tenures or stagnation indicators)")
        if signals.candidate_integrity < 0.5:
            why_not.append("Profile integrity weaker than peers (completeness/verification/consistency)")

        # Risks
        if float(features.get("keyword_stuffing_penalty", 1.0)) < 1.0:
            risks.append("Potential keyword stuffing; verify claims via deep-dive interview")
        if float(features.get("llm_only_recency_penalty", 1.0)) < 1.0:
            risks.append("AI/LLM experience appears recent; validate depth and fundamentals")
        if int(features.get("notice_period_days", 0) or 0) > 60:
            risks.append("Long notice period may delay hiring")
        if not features.get("open_to_work"):
            risks.append("Not marked open-to-work; engagement may be lower")

        # Recruiter summary (short, grounded, demo-friendly)
        yoe = float(features.get("derived_years_exp", 0.0) or features.get("years_exp", 0.0) or 0.0)
        loc = str(features.get("location_city", "") or "")
        prod = _fmt_pct(float(features.get("production_evidence_score", 0.0) or 0.0))
        notice = int(features.get("notice_period_days", 0) or 0)
        otw = "open to work" if features.get("open_to_work") else "passive"
        top_strength = why_selected[0] if why_selected else "Relevant AI/ML background"
        recruiter_summary = (
            f"{yoe:.1f}y exp; {top_strength}; production {prod}; "
            f"{otw}; {notice}d notice; {loc or 'India'}."
        )[:300]

        # Dedupe while preserving order
        def dedupe(xs: list[str]) -> list[str]:
            seen: set[str] = set()
            out: list[str] = []
            for x in xs:
                k = x.strip().lower()
                if not k or k in seen:
                    continue
                seen.add(k)
                out.append(x.strip())
            return out

        return Explainability(
            candidate_id=candidate_id,
            why_selected=dedupe(why_selected),
            why_not_selected=dedupe(why_not),
            missing_skills=dedupe(missing),
            risks=dedupe(risks),
            recruiter_summary=recruiter_summary,
        )

