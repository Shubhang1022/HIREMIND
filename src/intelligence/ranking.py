from __future__ import annotations

from dataclasses import dataclass

from src.intelligence.types import JobUnderstanding, RankingSignals


def _clamp01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return float(x)


@dataclass
class AIRankingEngine:
    """Multi-signal ranking engine (deterministic, production-friendly).

    It consumes existing structured features (from `StructuredFeatureExtractor`)
    plus a semantic similarity score (already pool-normalized to [0,1] if desired).
    """

    weights: dict[str, float] | None = None

    def score_candidate(
        self,
        *,
        features: dict,
        job: JobUnderstanding | None = None,
        semantic_similarity: float,
    ) -> RankingSignals:
        # Semantic fit: start with embedding similarity, apply stuffing/recency penalties if present.
        semantic = semantic_similarity
        semantic *= float(features.get("keyword_stuffing_penalty", 1.0) or 1.0)
        semantic *= float(features.get("llm_only_recency_penalty", 1.0) or 1.0)
        semantic = _clamp01(semantic)

        # Experience fit: combine YoE, product ratio, production evidence.
        yoe = float(features.get("years_exp", 0.0) or 0.0)
        derived = float(features.get("derived_years_exp", 0.0) or 0.0)
        conservative_yoe = min(yoe if yoe > 0 else derived, derived if derived > 0 else yoe)
        exp_years = self._score_yoe(conservative_yoe)
        product_ratio = float(features.get("product_company_ratio", 0.0) or 0.0)
        production = float(features.get("production_evidence_score", 0.0) or 0.0)
        experience_fit = _clamp01(0.35 * exp_years + 0.35 * product_ratio + 0.30 * production)

        # Leadership: take extracted leadership evidence score if present.
        leadership = _clamp01(float(features.get("leadership_evidence_score", 0.0) or 0.0))

        # Behavioral signals: proxy from response rate + interview completion + trust.
        response_rate = float(features.get("recruiter_response_rate", 0.0) or 0.0)
        interview_completion = float(features.get("interview_completion_rate", 0.0) or 0.0)
        trust = (
            (1.0 if features.get("verified_email") else 0.0)
            + (1.0 if features.get("verified_phone") else 0.0)
            + (1.0 if features.get("linkedin_connected") else 0.0)
        ) / 3.0
        behavioral = _clamp01(0.50 * response_rate + 0.30 * interview_completion + 0.20 * trust)

        # Hiring readiness: open_to_work + recency + notice.
        open_to_work = 1.0 if features.get("open_to_work") else 0.0
        days_since = int(features.get("days_since_active", 9999) or 9999)
        recency = 1.0 if days_since <= 7 else (0.8 if days_since <= 30 else (0.6 if days_since <= 60 else (0.4 if days_since <= 90 else 0.2)))
        notice = int(features.get("notice_period_days", 180) or 180)
        notice_score = 1.0 if notice <= 30 else (0.7 if notice <= 60 else (0.5 if notice <= 90 else 0.3))
        hiring_readiness = _clamp01(0.40 * open_to_work + 0.30 * recency + 0.30 * notice_score)

        # Career stability: tenure stability + job hop penalty + stagnation penalty.
        longest_tenure = float(features.get("longest_tenure_months", 0.0) or 0.0)
        stability = _clamp01(min(longest_tenure / 48.0, 1.0))  # 4y tenure => 1.0
        job_hop_penalty = float(features.get("job_hop_penalty", 1.0) or 1.0)
        stagnation_penalty = float(features.get("stagnation_penalty", 0.0) or 0.0)
        career_stability = _clamp01(stability * job_hop_penalty * (1.0 - stagnation_penalty))

        # Candidate integrity: profile integrity + honeypot suspicion.
        completeness = float(features.get("profile_completeness_score", 0.0) or 0.0) / 100.0
        integrity = _clamp01(0.7 * completeness + 0.3 * trust)
        if features.get("honeypot_suspicion_score", 0.0):
            integrity = _clamp01(integrity * (1.0 - min(float(features["honeypot_suspicion_score"]), 1.0) * 0.5))
        if features.get("is_disqualified"):
            integrity = 0.0

        return RankingSignals(
            semantic_fit=semantic,
            experience_fit=experience_fit,
            leadership=leadership,
            behavioral_signals=behavioral,
            hiring_readiness=hiring_readiness,
            career_stability=career_stability,
            candidate_integrity=integrity,
        )

    def final_score(self, signals: RankingSignals) -> float:
        return signals.final(self.weights)

    def _score_yoe(self, years: float) -> float:
        # Calibrated to RankingLogic.md experience sweet-spot (6–8 years).
        y = float(years or 0.0)
        if y < 3:
            return max(0.3, y / 3 * 0.5)
        if y < 5:
            return 0.5 + (y - 3) / 2 * 0.2
        if y <= 6:
            return 0.7 + (y - 5) * 0.2
        if y <= 8:
            return 0.9 + (y - 6) / 2 * 0.1
        if y <= 10:
            return 1.0 - (y - 8) / 2 * 0.1
        if y <= 15:
            return 0.9 - (y - 10) / 5 * 0.15
        return max(0.65, 0.75 - (y - 15) * 0.02)

