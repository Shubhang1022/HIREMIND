from __future__ import annotations

from dataclasses import dataclass

from src.intelligence.types import InterviewPlan, JobUnderstanding


def _dedupe(xs: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in xs:
        k = x.strip().lower()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(x.strip())
    return out


@dataclass
class InterviewCopilot:
    """Generate personalized interview questions (deterministic templates)."""

    max_questions: int = 10

    def generate(
        self,
        *,
        candidate_id: str,
        features: dict,
        job: JobUnderstanding,
        missing_skills: list[str] | None = None,
        risks: list[str] | None = None,
    ) -> InterviewPlan:
        missing_skills = missing_skills or []
        risks = risks or []

        questions: list[str] = []

        # Core: retrieval system design
        questions.append(
            "Walk me through a retrieval or semantic search system you built end-to-end. What were the offline metrics (e.g., NDCG/MRR) and how did they correlate with online outcomes?"
        )

        # Embeddings specifics
        questions.append(
            "How do you choose an embedding model (e.g., BGE/E5) and tune chunking, indexing, and refresh strategy? What failures have you seen (drift, regression, latency)?"
        )

        # Vector DB tradeoffs
        if features.get("has_vector_db"):
            questions.append(
                "You list vector search experience. Compare Pinecone/Weaviate/Qdrant/FAISS trade-offs you encountered (filtering, hybrid search, consistency, cost)."
            )
        else:
            questions.append(
                "Assume you need ANN retrieval tomorrow. How would you evaluate Pinecone vs FAISS vs OpenSearch for a production workload with filters and updates?"
            )

        # Leadership / ownership
        if float(features.get("leadership_evidence_score", 0.0) or 0.0) >= 0.5:
            questions.append(
                "Tell me about a time you led a project across teams. What was the hardest technical decision, and how did you drive alignment?"
            )
        else:
            questions.append(
                "Describe a situation where you had to take ownership in ambiguity. How did you break the problem down and deliver?"
            )

        # Production evidence validation
        questions.append(
            "Pick one production ML system from your resume. What was the deployment topology, monitoring, rollback plan, and how did you debug one real incident?"
        )

        # Hiring readiness / logistics (keep it light)
        notice = int(features.get("notice_period_days", 0) or 0)
        if notice > 60:
            questions.append(
                f"Your notice period is {notice} days. What flexibility do you have (buyout/early release), and what start date could you realistically commit to?"
            )

        # Missing skills targeted questions
        for ms in missing_skills:
            if "evaluation" in ms.lower():
                questions.append(
                    "Design an evaluation framework for a retrieval/ranking system. What datasets, labels, metrics, and regression tests would you implement?"
                )
            if "vector" in ms.lower():
                questions.append(
                    "How would you implement hybrid search (dense + sparse) with metadata filters and ensure predictable latency at p99?"
                )
            if "python" in ms.lower():
                questions.append(
                    "Let’s do a Python deep dive: show how you’d structure a production-grade ranking service (types, testing, logging, performance)."
                )
            if "embedding" in ms.lower():
                questions.append(
                    "Explain bi-encoders vs cross-encoders in retrieval. When would you add reranking and how do you measure benefit?"
                )

        # Risk-driven probes
        for r in risks:
            if "keyword stuffing" in r.lower():
                questions.append(
                    "Some profiles over-index on buzzwords. Pick one claimed technology and explain exactly what you implemented, including code-level details."
                )
            if "recent" in r.lower():
                questions.append(
                    "Your AI experience seems relatively recent. Walk through the fundamentals: bias/variance, regularization, and how they show up in real systems."
                )

        # Behavioral expectations from JD
        if any("0" in b or "ownership" in b for b in (job.behavioral_expectations or [])):
            questions.append(
                "This role is 0→1. Tell me about something you shipped with incomplete requirements. How did you validate scope and avoid rework?"
            )

        return InterviewPlan(candidate_id=candidate_id, questions=_dedupe(questions)[: self.max_questions])

