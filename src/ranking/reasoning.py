"""ReasoningGenerator — produce grounded, fact-based reasoning strings.

Templates are loaded from config/reasoning_templates.yaml and selected
deterministically via hash(candidate_id) % len(tier_templates) to
ensure reproducibility while varying the output.

All facts come exclusively from the *features* dict — no hallucination.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.scoring.dimensions import DimScores
from src.intelligence.explainability import ExplainabilityEngine
from src.intelligence.ranking import AIRankingEngine
from src.intelligence.understanding import JobUnderstandingEngine

# ---------------------------------------------------------------------------
# Seniority-level → human-readable label
# ---------------------------------------------------------------------------
_SENIORITY_LABEL = {
    1: "Junior Engineer",
    2: "Software Engineer",
    3: "Senior Engineer",
    4: "Lead Engineer",
    5: "Principal Engineer",
    6: "Engineering Director",
}

# ---------------------------------------------------------------------------
# Dimension name → human phrase for strength/gap
# ---------------------------------------------------------------------------
_DIM_STRENGTH_PHRASE = {
    "specialization_match": "strong role specialization match",
    "required_skills_match": "high required skill overlap",
    "relevant_experience": "relevant AI/ML engineering experience",
    "semantic_similarity": "strong semantic alignment",
    "career_growth": "clear career progression",
    "behavioral_fit": "good behavioral fit",
    "integrity": "verified profile integrity",
    "education": "strong educational background",
}

_DIM_GAP_PHRASE = {
    "specialization_match": "mismatched role specialization",
    "required_skills_match": "gaps in required skills",
    "relevant_experience": "limited relevant AI experience",
    "semantic_similarity": "low semantic similarity",
    "career_growth": "flat career growth",
    "behavioral_fit": "lower behavioral signals",
    "integrity": "incomplete profile verification",
    "education": "non-preferred education background",
}

# Tier boundaries (inclusive)
_TIER_TOP10 = (1, 10)
_TIER_MID = (11, 50)
_TIER_LOWER = (51, 100)


def _get_tier(rank: int) -> str:
    if rank <= 10:
        return "top_10"
    elif rank <= 50:
        return "mid_11_50"
    return "lower_51_100"


class ReasoningGenerator:
    """Generate fact-grounded reasoning strings from actual candidate data."""

    def __init__(
        self, templates_path: str = "config/reasoning_templates.yaml"
    ) -> None:
        templates_file = Path(templates_path)
        if not templates_file.is_absolute():
            templates_file = _PROJECT_ROOT / templates_path

        with open(templates_file, "r", encoding="utf-8") as fh:
            self._templates: dict[str, list[str]] = yaml.safe_load(fh)

        # Intelligence-layer helpers (deterministic, safe fallbacks)
        self._job_engine = JobUnderstandingEngine()
        self._ai_ranker = AIRankingEngine()
        self._explain = ExplainabilityEngine()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        candidate_id: str,
        features: dict,
        scores: DimScores,
        rank: int,
    ) -> str:
        """Generate a reasoning string (≤300 chars) for one candidate.

        Uses only facts from *features* — no hallucination.
        Template is selected deterministically via hash(candidate_id).
        """
        # Score breakdown string
        if scores:
            breakdown = (
                f"Breakdown: Spec:{scores.specialization_match:.2f}, "
                f"Req:{scores.required_skills_match:.2f}, "
                f"Exp:{scores.relevant_experience:.2f}, "
                f"Sem:{scores.semantic_similarity:.2f}, "
                f"Gr:{scores.career_growth:.2f}, "
                f"Beh:{scores.behavioral_fit:.2f}, "
                f"Int:{scores.integrity:.2f}, "
                f"Edu:{scores.education:.2f}"
            )
        else:
            breakdown = "Breakdown: N/A"

        # Prefer intelligence-layer recruiter summary when possible.
        try:
            from src.features.text_builder import build_jd_text

            job = self._job_engine.extract(build_jd_text())
            signals = self._ai_ranker.score_candidate(
                features=features,
                job=job,
                semantic_similarity=float(scores.semantic_similarity) if scores else 0.0,
            )
            exp = self._explain.explain(
                candidate_id=candidate_id,
                features=features,
                job=job,
                signals=signals,
            )
            if exp.recruiter_summary:
                filled = f"{exp.recruiter_summary} | {breakdown}"
                return self._truncate(filled)
        except Exception:
            pass

        tier = _get_tier(rank)
        tier_templates = self._templates.get(tier, [])

        if not tier_templates:
            facts = self.extract_facts(candidate_id, features, scores)
            filled = (
                f"{facts['yoe']}y exp; {facts['title']}; {facts['top_skills']}; "
                f"{facts['location']}; {facts['notice_days']}d notice."
            )
            return self._truncate(f"{filled} | {breakdown}")

        template = tier_templates[hash(candidate_id) % len(tier_templates)]
        facts = self.extract_facts(candidate_id, features, scores)

        try:
            filled = template.format(**facts)
        except KeyError:
            filled = (
                f"{facts['yoe']}y; {facts['title']}; {facts['top_skills']}; "
                f"{facts['location']}; {facts['notice_days']}d notice. "
                f"{facts['main_strength']}."
            )

        return self._truncate(f"{filled} | {breakdown}")

    def extract_facts(
        self,
        candidate_id: str,
        features: dict,
        scores: DimScores,
    ) -> dict:
        """Extract key facts from features for template filling.

        Returns
        -------
        dict with keys:
            yoe, title, top_skills, location, notice_days,
            main_strength, main_gap
        """
        # --- Years of experience ---
        yoe_raw = features.get("relevant_years_exp") or features.get("years_exp") or 0.0
        yoe = f"{float(yoe_raw):.1f}"

        # --- Title (derived from seniority level + skill flags) ---
        seniority = int(features.get("current_seniority_level") or 2)
        base_label = _SENIORITY_LABEL.get(seniority, "Engineer")

        # Refine with skill signals
        has_emb = features.get("has_embedding_retrieval", False)
        has_vdb = features.get("has_vector_db", False)
        has_eval = features.get("has_evaluation_framework", False)
        has_py = features.get("has_python_advanced", False)

        if has_emb or has_vdb or has_eval:
            domain = "AI"
        elif has_py:
            domain = "Software"
        else:
            domain = "Technical"

        title = f"{base_label.replace('Engineer', domain + ' Engineer')}"
        if len(title) > 30:
            title = f"{domain} Engineer (L{seniority})"

        # --- Top skills (2-3 most relevant to JD) ---
        skill_parts: list[str] = []
        if has_emb:
            skill_parts.append("embeddings")
        if has_vdb:
            skill_parts.append("vector DB")
        if has_eval:
            skill_parts.append("eval frameworks")
        if has_py and len(skill_parts) < 3:
            skill_parts.append("Python")

        if not skill_parts:
            count = features.get("ai_ml_skill_count", 0)
            skill_parts = [f"{count} AI/ML skills" if count else "general ML"]

        top_skills = ", ".join(skill_parts[:3])

        # --- Location ---
        location = str(features.get("location_city") or "India")
        if len(location) > 20:
            location = location[:20]

        # --- Notice period ---
        notice_days = int(features.get("notice_period_days") or 30)

        # --- Main strength: highest dimension score ---
        if scores:
            dim_map = {
                "specialization_match": scores.specialization_match,
                "required_skills_match": scores.required_skills_match,
                "relevant_experience": scores.relevant_experience,
                "semantic_similarity": scores.semantic_similarity,
                "career_growth": scores.career_growth,
                "behavioral_fit": scores.behavioral_fit,
                "integrity": scores.integrity,
                "education": scores.education,
            }
            best_dim = max(dim_map, key=lambda k: dim_map[k])
            main_strength = _DIM_STRENGTH_PHRASE[best_dim]
            worst_dim = min(dim_map, key=lambda k: dim_map[k])
            main_gap = _DIM_GAP_PHRASE[worst_dim]
        else:
            main_strength = "general qualifications"
            main_gap = "limited evaluation data"

        if notice_days > 90:
            main_gap = f"long notice ({notice_days}d)"
        elif features.get("consulting_only", False):
            main_gap = "consulting-only background"
        else:
            main_gap = main_gap if main_gap != "limited evaluation data" else "insufficient data"

        return {
            "yoe": yoe,
            "title": title,
            "top_skills": top_skills,
            "location": location,
            "notice_days": notice_days,
            "main_strength": main_strength,
            "main_gap": main_gap,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _truncate(text: str, max_chars: int = 300) -> str:
        """Truncate to at most *max_chars* characters, preserving readability."""
        text = text.strip()
        if len(text) <= max_chars:
            return text
        truncated = text[:max_chars - 1]
        last_space = truncated.rfind(" ")
        if last_space > max_chars // 2:
            truncated = truncated[:last_space]
        return truncated.rstrip(".,;: |") + "…"
