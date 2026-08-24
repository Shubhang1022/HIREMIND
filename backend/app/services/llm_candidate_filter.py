"""LLM-based candidate filtering service.

Replaces the memory-intensive embedding model with OpenRouter LLM API calls.
This eliminates ~300-400MB RAM usage from SentenceTransformer model.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from app.core.config import settings
from app.core.openrouter import chat, InsufficientCreditsError

logger = logging.getLogger(__name__)

CANDIDATE_FILTER_SYSTEM_PROMPT = """You are an expert technical recruiter screening candidates for a job opening.

Your task is to evaluate each candidate and provide:
1. A relevance score (0.0 to 1.0) - how well they match the role
2. Role specialization match - how well their career specialization fits
3. Skills match score - coverage of required skills
4. Experience relevance - quality and relevance of their experience
5. A filter decision - should they pass or be filtered out
6. A brief summary - 1-2 sentence assessment

IMPORTANT RULES:
- Candidates with relevance_score < 0.3 should be filtered (filter_decision = "filter")
- Consider role-specific skills heavily (e.g., FAISS, Pinecone for Retrieval Engineer)
- Do not rank highly for generic AI/ML skills without role-specific depth
- Reward exact specialization match
- Be critical - not every candidate should score above 0.7

Return ONLY a valid JSON array, no markdown, no explanation."""


def _build_candidate_summary(candidate: dict) -> dict:
    """Extract key candidate info for LLM analysis."""
    profile = candidate.get("profile", {})
    career_history = candidate.get("career_history", [])
    skills = candidate.get("skills", [])
    
    # Extract skills list
    skills_list = []
    for s in skills:
        if isinstance(s, str):
            skills_list.append(s)
        elif isinstance(s, dict) and "name" in s:
            skills_list.append(s["name"])
    
    # Extract companies
    companies = []
    for job in career_history[:5]:  # Last 5 jobs
        if isinstance(job, dict):
            companies.append(job.get("company", ""))
    
    return {
        "candidate_id": candidate.get("candidate_id", "unknown"),
        "current_title": profile.get("current_title", ""),
        "years_of_experience": profile.get("years_of_experience", 0),
        "skills": skills_list[:15],  # Top 15 skills
        "companies": companies,
        "candidate_specialization": candidate.get("candidate_specialization", ""),
        "candidate_quality_score": candidate.get("candidate_quality_score", 0),
        "is_disqualified": candidate.get("is_disqualified", False),
    }


async def analyze_candidates_batch(
    candidates: list[dict],
    jd_text: str,
    jd_title: str,
    batch_size: int = 10,
) -> list[dict]:
    """
    Analyze candidates using LLM instead of embeddings.
    
    Args:
        candidates: List of enriched candidate dicts
        jd_text: Full job description text
        jd_title: Job title
        batch_size: Number of candidates per LLM call (default 10)
    
    Returns:
        List of analysis results with:
        - candidate_id
        - relevance_score (0.0-1.0)
        - role_specialization_match (0.0-1.0)
        - skills_match_score (0.0-1.0)
        - experience_relevance (0.0-1.0)
        - filter_decision ("pass" or "filter")
        - filter_reason
        - llm_summary
    """
    if not candidates:
        return []
    
    if not settings.openrouter_api_key:
        logger.warning("OpenRouter API key not configured. Using fallback scoring.")
        return _fallback_analysis(candidates)
    
    # Prepare candidate summaries (lighter payload for LLM)
    summaries = [_build_candidate_summary(c) for c in candidates]
    
    results = []
    
    # Process in batches
    for i in range(0, len(summaries), batch_size):
        batch = summaries[i:i + batch_size]
        batch_results = await _analyze_batch(batch, jd_text, jd_title)
        results.extend(batch_results)
        
        # Small delay between batches to avoid rate limiting
        if i + batch_size < len(summaries):
            await asyncio.sleep(0.5)
    
    return results


async def _analyze_batch(
    batch: list[dict],
    jd_text: str,
    jd_title: str,
) -> list[dict]:
    """Analyze a single batch of candidates."""
    
    candidates_json = json.dumps(batch, indent=2, default=str)
    
    prompt = f"""Evaluate these candidates for the role:

JOB TITLE: {jd_title}

JOB DESCRIPTION (summary):
{jd_text[:2000]}

CANDIDATES TO EVALUATE:
{candidates_json}

Return a JSON array with one object per candidate. Each object must have exactly these keys:
{{
  "candidate_id": "from input",
  "relevance_score": 0.0-1.0,
  "role_specialization_match": 0.0-1.0,
  "skills_match_score": 0.0-1.0,
  "experience_relevance": 0.0-1.0,
  "filter_decision": "pass" or "filter",
  "filter_reason": "brief explanation",
  "llm_summary": "1-2 sentence assessment"
}}

RULES:
- relevance_score < 0.3 means filter (not qualified)
- Reward role-specific skills over generic ones
- Be critical and realistic
- Sort by relevance_score descending
- Return ONLY the JSON array, no other text"""

    try:
        result = await chat(
            prompt,
            system=CANDIDATE_FILTER_SYSTEM_PROMPT,
            max_tokens=2000,
            temperature=0.2,
        )
    except InsufficientCreditsError:
        logger.error("OpenRouter insufficient credits. Using fallback scoring.")
        return _fallback_analysis_batch(batch)
    except Exception as e:
        logger.error("LLM analysis failed: %s. Using fallback.", e)
        return _fallback_analysis_batch(batch)
    
    if not result:
        logger.warning("LLM returned empty response. Using fallback.")
        return _fallback_analysis_batch(batch)
    
    # Parse JSON response
    result = result.strip()
    
    # Extract JSON array
    start = result.find("[")
    end = result.rfind("]")
    if start != -1 and end != -1:
        result = result[start:end + 1]
    
    try:
        analyses = json.loads(result)
        if isinstance(analyses, list):
            # Validate each result
            valid_results = []
            for a in analyses:
                if _validate_analysis(a):
                    valid_results.append(a)
                else:
                    # Use fallback for invalid entries
                    logger.warning("Invalid analysis format for candidate. Using fallback.")
                    fallback = _fallback_single(
                        next((c for c in batch if c.get("candidate_id") == a.get("candidate_id")), batch[0])
                    )
                    valid_results.append(fallback)
            return valid_results
    except json.JSONDecodeError as e:
        logger.error("Failed to parse LLM response as JSON: %s", e)
    
    return _fallback_analysis_batch(batch)


def _validate_analysis(obj: dict) -> bool:
    """Validate LLM analysis result has required fields."""
    if not isinstance(obj, dict):
        return False
    
    required = [
        "candidate_id",
        "relevance_score",
        "role_specialization_match",
        "skills_match_score",
        "experience_relevance",
        "filter_decision",
        "filter_reason",
        "llm_summary",
    ]
    
    for key in required:
        if key not in obj:
            return False
    
    # Validate types
    if not isinstance(obj["candidate_id"], str):
        return False
    if not isinstance(obj["relevance_score"], (int, float)):
        return False
    if obj["filter_decision"] not in ("pass", "filter"):
        return False
    
    return True


def _fallback_analysis(candidates: list[dict]) -> list[dict]:
    """Generate fallback analysis when LLM is unavailable."""
    return [_fallback_single(c) for c in candidates]


def _fallback_analysis_batch(batch: list[dict]) -> list[dict]:
    """Generate fallback analysis for a batch."""
    return [_fallback_single(c) for c in batch]


def _fallback_single(candidate: dict) -> dict:
    """Generate deterministic analysis for a single candidate."""
    quality_score = candidate.get("candidate_quality_score", 0.5)
    is_disqualified = candidate.get("is_disqualified", False)
    
    # Base score from quality score
    relevance = min(1.0, max(0.0, quality_score / 100.0))
    
    # Disqualified candidates get very low score
    if is_disqualified:
        relevance = 0.1
    
    # Simple heuristic scoring
    yoe = float(candidate.get("years_of_experience", 0))
    exp_relevance = min(1.0, yoe / 10.0)  # 10 years = max
    
    # Skills score based on count (simple heuristic)
    skills = candidate.get("skills", [])
    skills_count = len(skills) if isinstance(skills, list) else 0
    skills_score = min(1.0, skills_count / 10.0)  # 10 skills = max
    
    filter_decision = "filter" if relevance < 0.3 else "pass"
    
    return {
        "candidate_id": candidate.get("candidate_id", "unknown"),
        "relevance_score": round(relevance, 3),
        "role_specialization_match": round(relevance * 0.9, 3),
        "skills_match_score": round(skills_score, 3),
        "experience_relevance": round(exp_relevance, 3),
        "filter_decision": filter_decision,
        "filter_reason": "Fallback scoring (LLM unavailable)" if relevance < 0.3 else "Passed quality threshold",
        "llm_summary": f"Quality score: {quality_score:.1f}, Experience: {yoe:.1f}y, Skills: {skills_count}",
    }
