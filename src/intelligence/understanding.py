from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from src.features.structured import infer_seniority
from src.intelligence.types import CandidateUnderstanding, ExtractedSkill, JobUnderstanding


_LEADERSHIP_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bled\b.*\bteam\b", re.I), "Led a team"),
    (re.compile(r"\bmanaged\b.*\bteam\b", re.I), "Managed a team"),
    (re.compile(r"\bmentored?\b", re.I), "Mentored others"),
    (re.compile(r"\btech(nical)?\s+lead\b", re.I), "Technical leadership"),
    (re.compile(r"\bowned\b.*\b(platform|system|service)\b", re.I), "Owned a system/platform"),
    (re.compile(r"\barchitect(ed|ure)\b", re.I), "Architecture ownership"),
    (re.compile(r"\bcross[\s-]functional\b", re.I), "Cross-functional leadership"),
]

_DOMAIN_HINTS: dict[str, list[str]] = {
    "search/retrieval": ["retrieval", "semantic search", "vector", "embedding", "ann", "hybrid search"],
    "ranking/recsys": ["ranking", "ranker", "recommendation", "recommender", "ltr", "lambdamart", "ndcg", "mrr"],
    "nlp/llm": ["nlp", "transformer", "bert", "gpt", "llm", "fine-tun", "lora", "qlora", "peft"],
    "cv": ["computer vision", "opencv", "image", "detection", "segmentation"],
    "mlops/serving": ["mlops", "model serving", "inference", "monitoring", "feature store", "drift"],
    "data/analytics": ["sql", "warehouse", "spark", "airflow", "etl", "dbt", "analytics"],
}


def _norm_skill(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _dedupe_preserve(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        k = it.strip()
        if not k:
            continue
        if k.lower() in seen:
            continue
        seen.add(k.lower())
        out.append(k)
    return out


@dataclass
class CandidateUnderstandingEngine:
    """Extract human-interpretable signals from a raw candidate dict.

    This is deterministic, grounded in the candidate's own fields, and
    safe for offline / contest constraints.
    """

    max_skill_evidence: int = 2

    def extract(self, candidate: dict[str, Any]) -> CandidateUnderstanding:
        profile = candidate.get("profile", {}) or {}
        career = candidate.get("career_history", []) or []
        skills = candidate.get("skills", []) or []

        candidate_id = str(candidate.get("candidate_id", ""))
        years = float(profile.get("years_of_experience", 0.0) or 0.0)

        headline = str(profile.get("headline", "") or "").strip()
        summary = str(profile.get("summary", "") or "").strip()
        current_title = str(profile.get("current_title", "") or "").strip()

        # Skills: merge explicit skills + salient mentions in descriptions.
        extracted_skills: dict[str, ExtractedSkill] = {}
        for s in skills:
            name = _norm_skill(str(s.get("name", "")))
            if not name:
                continue
            extracted_skills[name] = ExtractedSkill(
                name=name,
                evidence=[],
                proficiency=(s.get("proficiency") or None),
            )

        all_desc = " ".join(str(r.get("description", "") or "") for r in career).strip()
        # Evidence snippets: take the sentence fragment around the mention.
        for sk in list(extracted_skills.keys()):
            if sk in all_desc.lower():
                extracted_skills[sk] = ExtractedSkill(
                    name=sk,
                    evidence=self._evidence_snippets(all_desc, sk),
                    proficiency=extracted_skills[sk].proficiency,
                )

        # Leadership signals from descriptions
        leadership = self._leadership_signals(all_desc)

        # Domain expertise from hints
        domain = self._domain_expertise((headline + " " + summary + " " + all_desc).lower())

        # Career growth signals (title seniority trajectory + promotions)
        growth = self._career_growth_signals(career, current_title=current_title)

        experience_summary = " ".join([p for p in (headline, summary) if p])[:800].strip()
        if not experience_summary:
            experience_summary = (all_desc[:800] if all_desc else "").strip()

        return CandidateUnderstanding(
            candidate_id=candidate_id,
            experience_summary=experience_summary,
            years_experience=years,
            skills=sorted(extracted_skills.values(), key=lambda x: x.name),
            leadership_signals=leadership,
            domain_expertise=domain,
            career_growth_signals=growth,
        )

    def _evidence_snippets(self, text: str, needle: str) -> list[str]:
        # Very lightweight "sentence" segmentation.
        lower = text.lower()
        idx = lower.find(needle.lower())
        if idx < 0:
            return []
        start = max(0, idx - 120)
        end = min(len(text), idx + 120)
        snippet = text[start:end].strip()
        # Keep it compact and readable.
        snippet = re.sub(r"\s+", " ", snippet)
        return [snippet[:240]] if snippet else []

    def _leadership_signals(self, text: str) -> list[str]:
        hits: list[str] = []
        for pat, label in _LEADERSHIP_PATTERNS:
            if pat.search(text):
                hits.append(label)
        return _dedupe_preserve(hits)

    def _domain_expertise(self, text_lower: str) -> list[str]:
        out: list[str] = []
        for domain, hints in _DOMAIN_HINTS.items():
            if any(h in text_lower for h in hints):
                out.append(domain)
        return _dedupe_preserve(out)

    def _career_growth_signals(self, career: list[dict[str, Any]], *, current_title: str) -> list[str]:
        if not career:
            return []

        # Sort by start_date ascending (best-effort, strings).
        sorted_roles = sorted(career, key=lambda r: str(r.get("start_date", "")))
        titles = [str(r.get("title", "") or "") for r in sorted_roles if r.get("title")]
        levels = [infer_seniority(t) for t in titles] if titles else []

        signals: list[str] = []
        if levels:
            if all(levels[i] <= levels[i + 1] for i in range(len(levels) - 1)):
                signals.append("Seniority trajectory is non-decreasing")
            if len(levels) >= 2 and levels[-1] - levels[0] >= 2:
                signals.append("Strong seniority growth over career")
            if current_title and ("founding" in current_title.lower() or "founder" in current_title.lower()):
                signals.append("Founding/entrepreneurial exposure")

        # Promotion hints in descriptions
        all_desc = " ".join(str(r.get("description", "") or "") for r in sorted_roles)
        if re.search(r"\bpromot(ed|ion)\b", all_desc, re.I):
            signals.append("Explicit promotions mentioned")

        return _dedupe_preserve(signals)


@dataclass
class JobUnderstandingEngine:
    """Extract structured intent from a job description text."""

    def extract(self, jd_text: str) -> JobUnderstanding:
        text = (jd_text or "").strip()
        lower = text.lower()

        role = self._infer_role(lower)
        seniority = self._infer_seniority(lower)
        required = self._extract_required_skills(lower)
        preferred = self._extract_preferred_skills(lower)
        behavioral = self._extract_behavioral_expectations(lower)
        domains = self._domain_expertise(lower)
        min_exp = self._infer_min_experience(lower)

        summary = self._summarize(role, seniority, required, preferred, behavioral)

        return JobUnderstanding(
            role=role,
            seniority=seniority,
            required_skills=required,
            preferred_skills=preferred,
            behavioral_expectations=behavioral,
            job_summary=summary,
            domains=domains,
            min_experience=min_exp,
        )

    def _infer_role(self, lower: str) -> str:
        from src.features.structured import ROLES_KEYWORDS
        scores = {}
        for role_name, config in ROLES_KEYWORDS.items():
            score = 0.0
            
            for t_kw in config["titles"]:
                if t_kw in lower:
                    score += 12.0
            for s_kw in config["skills"]:
                if s_kw in lower:
                    score += 1.0
            for d_kw in config["desc"]:
                if d_kw in lower:
                    score += 0.5
            scores[role_name] = score
            
        best_role = "Backend Engineer"
        max_score = 0.0
        for r, s in scores.items():
            if s > max_score:
                max_score = s
                best_role = r
        return best_role

    def _infer_seniority(self, lower: str) -> str:
        if any(k in lower for k in ("principal", "staff", "lead", "architect")):
            return "Staff+"
        if "senior" in lower or "sr." in lower or "sr " in lower:
            return "Senior"
        if any(k in lower for k in ("junior", "intern", "entry")):
            return "Junior"
        return "Mid"

    def _extract_required_skills(self, lower: str) -> list[str]:
        # Minimal, robust heuristic: scan for canonical clusters.
        required = []
        if "python" in lower:
            required.append("python")
        if any(k in lower for k in ("embedding", "sentence-transformers", "bge", "e5", "semantic search")):
            required.append("embeddings / dense retrieval")
        if any(k in lower for k in ("pinecone", "weaviate", "qdrant", "milvus", "faiss", "vector database")):
            required.append("vector database / ANN search")
        if any(k in lower for k in ("ndcg", "mrr", "map", "evaluation", "offline evaluation", "a/b")):
            required.append("ranking/retrieval evaluation")
        return _dedupe_preserve(required)

    def _extract_preferred_skills(self, lower: str) -> list[str]:
        preferred = []
        if any(k in lower for k in ("lora", "qlora", "peft", "fine-tun")):
            preferred.append("LLM fine-tuning (LoRA/QLoRA/PEFT)")
        if any(k in lower for k in ("learning to rank", "ltr", "lambdamart", "xgboost")):
            preferred.append("learning-to-rank (LTR)")
        if any(k in lower for k in ("kubernetes", "docker", "mlops", "triton", "onnx")):
            preferred.append("production ML infrastructure")
        if any(k in lower for k in ("open source", "github")):
            preferred.append("open source contributions")
        return _dedupe_preserve(preferred)

    def _extract_behavioral_expectations(self, lower: str) -> list[str]:
        behavioral = []
        if any(k in lower for k in ("founding team", "founding", "0 to 1", "0-1")):
            behavioral.append("high ownership in ambiguous 0→1 environment")
        if any(k in lower for k in ("cross-functional", "stakeholder", "product")):
            behavioral.append("cross-functional collaboration with product stakeholders")
        if any(k in lower for k in ("communication", "write", "document")):
            behavioral.append("strong written communication and documentation")
        if any(k in lower for k in ("mentor", "leadership", "lead")):
            behavioral.append("mentoring / technical leadership")
        return _dedupe_preserve(behavioral)

    def _infer_min_experience(self, lower: str) -> float:
        # Search for patterns like: "5+ years of experience", "minimum 3 years", "experience: 8+ years"
        match = re.search(r"(\d+)\+?\s*(?:years?|yrs?)(?:\s+of)?\s+experience", lower)
        if match:
            return float(match.group(1))
        match = re.search(r"experience(?:\s+required)?\s*(?::|of)?\s*(\d+)\+?\s*(?:years?|yrs?)", lower)
        if match:
            return float(match.group(1))
        match = re.search(r"min(?:imum)?\s+(\d+)\+?\s*(?:years?|yrs?)", lower)
        if match:
            return float(match.group(1))
        return 0.0

    def _domain_expertise(self, text_lower: str) -> list[str]:
        out: list[str] = []
        for domain, hints in _DOMAIN_HINTS.items():
            if any(h in text_lower for h in hints):
                out.append(domain)
        return _dedupe_preserve(out)

    def _summarize(
        self,
        role: str,
        seniority: str,
        required: list[str],
        preferred: list[str],
        behavioral: list[str],
    ) -> str:
        req = ", ".join(required[:4]) if required else "not specified"
        pref = ", ".join(preferred[:3]) if preferred else "none"
        beh = ", ".join(behavioral[:3]) if behavioral else "standard"
        return f"{seniority} {role}. Required: {req}. Preferred: {pref}. Expectations: {beh}."

