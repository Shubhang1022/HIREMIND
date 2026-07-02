"""OpenRouter LLM client — thin async wrapper around the OpenAI-compatible API."""

from __future__ import annotations

import json
import logging
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: Optional[httpx.AsyncClient] = None


class InsufficientCreditsError(Exception):
    """Raised when OpenRouter returns 402 Payment Required or credit warnings."""
    pass

# ── Expert Recruiter System Prompt ────────────────────────────────────────────
RECRUITER_SYSTEM_PROMPT = """You are an expert Technical Recruiter, Hiring Manager, and Talent Intelligence Analyst.

Your task is NOT to perform keyword matching.
Your task is to evaluate candidates exactly like a senior recruiter would.

For every Job Description and Candidate Profile:

STEP 1: Understand the Job
Extract: Primary Role, Role Category, Seniority Level, Required Skills, Preferred Skills,
Domain Expertise, Tools & Technologies, Behavioral Expectations, Industry Context, Core Responsibilities.
Determine: "What type of person would actually succeed in this role?"

STEP 2: Understand the Candidate
Analyze: Career History, Skills, Projects, Education, Certifications, Experience,
Recruiter Activity, Behavioral Signals, Career Growth, Domain Experience.
Determine: "What type of professional is this candidate?"

STEP 3: Role Specialization Analysis
Identify candidate specialization (AI Engineer, ML Engineer, NLP Engineer, Retrieval Engineer,
Search Engineer, Recommendation Systems Engineer, Backend Engineer, Data Scientist, MLOps Engineer, etc.)
IMPORTANT: Do not treat all AI-related roles as equivalent.
A Retrieval Engineer is NOT the same as a Data Scientist.
A candidate should receive higher scores when their specialization matches the job specialization.

STEP 4: Weighted Recruiter Scoring
1. Role Specialization Match (30%) - How closely the candidate's actual career specialization matches the role.
2. Required Skills Match (20%) - Required skills only.
3. Domain Expertise Match (15%) - Relevant industry/domain experience.
4. Experience Relevance (10%) - Quality and relevance of experience. Do NOT reward irrelevant experience.
5. Career Progression (5%) - Promotions, increasing responsibility, leadership.
6. Project Impact (5%) - Meaningful project contributions.
7. Behavioral Fit (5%) - Recruiter activity and behavioral signals.
8. Integrity Score (5%) - Timeline consistency, realistic growth, profile quality.
9. Education & Certifications (5%) - Relevant educational background.

STEP 5: Penalize Generic Matching
Do NOT rank candidates highly simply because they have: Python, AI, Machine Learning, NLP, many years of experience.
These are generic skills. Instead reward role-specific expertise.
For Retrieval Engineer: Embeddings, Vector Search, FAISS, Pinecone, Weaviate, Retrieval, Search Systems, Ranking Systems.
For Data Scientist: Statistics, Experimentation, Forecasting, Analytics, Data Modeling.
For MLOps: Docker, Kubernetes, CI/CD, Monitoring, Deployment.

CRITICAL RULE: If the same candidate would rank #1 across multiple completely different job descriptions,
re-evaluate role specialization alignment. Different jobs should produce different top candidates
unless one candidate genuinely dominates every required specialization.
Think like a recruiter, not a keyword search engine."""


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=settings.openrouter_base_url,
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost:3000",
                "X-Title": "HireMind AI Copilot",
            },
            timeout=120.0,
        )
    return _client


async def chat(
    prompt: str,
    system: str = "You are a helpful AI assistant for a recruitment platform.",
    model: Optional[str] = None,
    max_tokens: int = 512,
    temperature: float = 0.3,
) -> str:
    """Send a single-turn chat prompt and return the text response."""
    if not settings.openrouter_api_key:
        return ""

    chosen_model = model or settings.openrouter_model

    payload = {
        "model": chosen_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    try:
        client = _get_client()
        resp = await client.post("/chat/completions", json=payload, timeout=60.0)
        if resp.status_code == 402:
            logger.error("OpenRouter insufficient credits (402): %s", resp.text)
            raise InsufficientCreditsError(f"OpenRouter credit failure: {resp.text}")
        if resp.status_code != 200:
            print(f"OpenRouter Error: status_code={resp.status_code}")
            print(f"OpenRouter Error Response: {resp.text}")
            logger.error("OpenRouter response error: status_code=%d, response_body=%s", resp.status_code, resp.text)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except InsufficientCreditsError:
        raise
    except Exception as exc:
        if hasattr(exc, "response") and exc.response is not None:
            status_code = exc.response.status_code
            response_text = exc.response.text
            print(f"OpenRouter Call Failed: status_code={status_code}")
            print(f"OpenRouter Call Failed Response: {response_text}")
            logger.warning(
                "OpenRouter call failed (%s): status_code=%d, response_body=%s",
                chosen_model,
                status_code,
                response_text,
            )
        else:
            print(f"OpenRouter Call Unexpected Error: {exc}")
            logger.warning("OpenRouter call failed (%s): %s", chosen_model, exc)
        raise exc


async def parse_jd_with_llm(jd_text: str) -> dict:
    """Extract structured fields from raw JD text."""
    prompt = f"""Extract structured information from this job description.
Return ONLY valid JSON with these exact keys:
{{
  "title": "job title",
  "company": "company name or null",
  "location": "location string or null",
  "experience_years": {{"min": 0, "max": 0}},
  "must_have_skills": ["skill1", "skill2"],
  "nice_to_have_skills": ["skill1"],
  "hard_disqualifiers": ["condition1"],
  "preferred_locations": ["city1", "city2"],
  "salary_range_lpa": {{"min": 0.0, "max": 0.0}},
  "role_category": "choose one from: MLOPS, DEVOPS, DATA_ENGINEERING, DATA_SCIENCE, AI_ML, FRONTEND, PROJECT_MANAGEMENT, PRODUCT_MANAGEMENT, DESIGN, MARKETING, HR, BACKEND",
  "seniority": "choose one from: Junior, Mid, Senior, Lead, Principal, Executive"
}}

Job Description:
{jd_text[:3000]}

Return only the JSON object, no markdown, no explanation."""

    try:
        result = await chat(
            prompt,
            system="You are a structured data extractor. Return only valid JSON.",
            max_tokens=600,
            temperature=0.1,
        )
    except InsufficientCreditsError:
        logger.error("Insufficient credits during parse_jd_with_llm.")
        return {}

    if not result:
        return {}

    result = result.strip()
    if result.startswith("```"):
        result = result.split("```")[1]
        if result.startswith("json"):
            result = result[4:]
    result = result.strip().rstrip("```").strip()

    try:
        return json.loads(result)
    except json.JSONDecodeError:
        logger.warning("LLM returned invalid JSON for JD parse: %s", result[:200])
        return {}


def validate_candidate_json(obj: dict) -> bool:
    """Validate that candidate object matches the strict JSON schema."""
    if obj is None or not isinstance(obj, dict):
        return False
    required_keys = {
        "candidate_id", "rank", "final_score", "role_specialization",
        "specialization_match_score", "skills_match_score", "experience_score",
        "behavioral_score", "integrity_score", "strengths", "weaknesses",
        "missing_skills", "recruiter_summary", "why_selected", "why_not_ranked_higher"
    }
    if not all(k in obj for k in required_keys):
        return False
    if not isinstance(obj["candidate_id"], str):
        return False
    if not isinstance(obj["rank"], (int, float)):
        return False
    if not isinstance(obj["final_score"], (int, float)):
        return False
    if not isinstance(obj["role_specialization"], str):
        return False
    if not isinstance(obj["specialization_match_score"], (int, float)):
        return False
    if not isinstance(obj["skills_match_score"], (int, float)):
        return False
    if not isinstance(obj["experience_score"], (int, float)):
        return False
    if not isinstance(obj["behavioral_score"], (int, float)):
        return False
    if not isinstance(obj["integrity_score"], (int, float)):
        return False
    if not isinstance(obj["strengths"], list) or not all(isinstance(x, str) for x in obj["strengths"]):
        return False
    if not isinstance(obj["weaknesses"], list) or not all(isinstance(x, str) for x in obj["weaknesses"]):
        return False
    if not isinstance(obj["missing_skills"], list) or not all(isinstance(x, str) for x in obj["missing_skills"]):
        return False
    if not isinstance(obj["recruiter_summary"], str):
        return False
    if not isinstance(obj["why_selected"], str):
        return False
    if not isinstance(obj["why_not_ranked_higher"], str):
        return False
    return True


async def recruiter_evaluate_batch(
    jd_text: str,
    jd_title: str,
    candidates_data: list[dict],
) -> tuple[list[dict], Optional[str]]:
    if not settings.openrouter_api_key:
        return [], "OpenRouter API key is missing in configuration."
    if not candidates_data:
        return [], None
    try:
        results = await _recruiter_evaluate_batch_impl(jd_text, jd_title, candidates_data)
        return results, None
    except InsufficientCreditsError as exc:
        import traceback
        traceback.print_exc()
        logger.warning("[WARNING] OpenRouter API returned 402. Switching to deterministic ranking.")
        return [], f"OpenRouter credit failure: {exc}"
    except Exception as exc:
        import traceback
        traceback.print_exc()
        logger.error("[WARNING] OpenRouter call failed: %s. Switching to deterministic ranking.", exc)
        return [], f"OpenRouter call failed: {exc}"


async def _recruiter_evaluate_batch_impl(
    jd_text: str,
    jd_title: str,
    candidates_data: list[dict],
) -> list[dict]:
    """
    Use the expert recruiter system prompt to evaluate a batch of candidates
    against the JD in ONE LLM call. Returns enriched evaluation for each candidate.
    Includes strict validation, backoff retry for transient errors, and auto-retry loop.
    """
    import asyncio
    
    if not settings.openrouter_api_key or not candidates_data:
        return []

    async def evaluate_subset(subset: list[dict], max_toks: int) -> list[dict]:
        candidates_json = json.dumps(subset, separators=(",", ":"), default=str)
        prompt = f"""You are evaluating candidates for the following role:

JOB TITLE: {jd_title}
JOB DESCRIPTION:
{jd_text[:2000]}

---
CANDIDATES TO EVALUATE (JSON):
{candidates_json}

---
INSTRUCTIONS:
Evaluate each candidate using the recruiter scoring methodology.
Return a JSON array — one object per candidate — sorted by final_score descending.

Each object MUST have these exact keys:
{{
  "candidate_id": "...",
  "rank": 1,
  "final_score": 0.85,
  "role_specialization": "e.g. Retrieval Engineer",
  "specialization_match_score": 0.90,
  "skills_match_score": 0.80,
  "experience_score": 0.75,
  "behavioral_score": 0.70,
  "integrity_score": 0.85,
  "strengths": ["strength1", "strength2"],
  "weaknesses": ["weakness1"],
  "missing_skills": ["skill1"],
  "recruiter_summary": "2-3 sentence expert assessment",
  "why_selected": "Why this rank",
  "why_not_ranked_higher": "What's holding them back (empty string for rank 1)"
}}

RULES:
- final_score must be between 0.0 and 1.0
- Different JDs must produce meaningfully different rankings
- Penalize generic AI/ML skills without role-specific depth
- Reward exact specialization match heavily (30% weight)
- Return ONLY the JSON array, no markdown, no explanation"""

        retries = 3
        backoff = 1.0
        last_exc = None
        for attempt in range(retries):
            try:
                result = await chat(
                    prompt,
                    system=RECRUITER_SYSTEM_PROMPT,
                    max_tokens=max_toks,
                    temperature=0.2 if attempt == 0 else 0.4,
                )
            except InsufficientCreditsError as e:
                raise e
            except Exception as e:
                last_exc = e
                status_code = getattr(getattr(e, "response", None), "status_code", None)
                if status_code in (400, 401, 403, 404):
                    raise e
                logger.warning("Attempt %d failed with transient error: %s. Retrying in %.1fs...", attempt + 1, e, backoff)
                await asyncio.sleep(backoff)
                backoff *= 2.0
                continue

            if not result:
                logger.warning("Attempt %d returned empty response.", attempt + 1)
                continue

            result = result.strip()
            if result.startswith("```"):
                parts = result.split("```")
                result = parts[1] if len(parts) > 1 else result
                if result.startswith("json"):
                    result = result[4:]
            result = result.strip().rstrip("```").strip()

            start = result.find("[")
            end = result.rfind("]")
            if start != -1 and end != -1:
                result = result[start:end + 1]

            try:
                evaluations = json.loads(result)
                if isinstance(evaluations, list) and len(evaluations) > 0:
                    if all(validate_candidate_json(ev) for ev in evaluations):
                        return evaluations
                    else:
                        logger.warning("Attempt %d: JSON validation failed for one or more candidates.", attempt + 1)
                else:
                     logger.warning("Attempt %d: JSON parsed but is not a non-empty list.", attempt + 1)
            except json.JSONDecodeError as exc:
                logger.warning("Attempt %d: JSON decode error: %s", attempt + 1, exc)

            prompt += "\n\nWARNING: Your previous response did not match the required JSON schema or was not valid JSON. Please ensure the response is EXACTLY a JSON array matching the keys and format requested, with NO other text."
        
        if last_exc:
            raise last_exc
        raise json.JSONDecodeError("Failed to parse or validate JSON response from LLM after 3 retries.", "", 0)

    try:
        return await evaluate_subset(candidates_data, max_toks=4000)
    except InsufficientCreditsError as e:
        logger.warning("Recruiter batch evaluation hit OpenRouter 402: %s. Retrying with reduced subset (top 5 candidates) and max_tokens=1000", e)
        try:
            subset = candidates_data[:5]
            return await evaluate_subset(subset, max_toks=1000)
        except Exception as retry_err:
            logger.error("Failed to run recruiter evaluation even with reduced subset: %s", retry_err)
            raise retry_err
    except Exception as e:
        logger.error("Recruiter batch evaluation failed: %s", e)
        raise e


async def generate_candidate_reasoning(
    candidate_features: dict,
    dim_scores,
    rank: int,
    jd_title: str = "Senior AI Engineer",
) -> str:
    """Fallback: per-candidate reasoning when batch evaluation is not used."""
    yoe = candidate_features.get("years_exp", 0)
    location = candidate_features.get("location_city", "India")
    notice = candidate_features.get("notice_period_days", 30)
    prod_score = candidate_features.get("production_evidence_score", 0)
    sem_score = round(getattr(dim_scores, "semantic_skill_fit", 0), 2)
    exp_score = round(getattr(dim_scores, "experience_quality", 0), 2)

    skills = []
    if candidate_features.get("has_embedding_retrieval"):
        skills.append("embedding/retrieval")
    if candidate_features.get("has_vector_db"):
        skills.append("vector DB")
    if candidate_features.get("has_evaluation_framework"):
        skills.append("eval frameworks")
    if candidate_features.get("has_python_advanced"):
        skills.append("Python")
    skills_str = ", ".join(skills) if skills else "general ML"

    tier = "top" if rank <= 10 else ("mid" if rank <= 50 else "lower")

    prompt = f"""Write a concise candidate ranking reasoning for rank #{rank} ({tier} tier).
Role: {jd_title}
Facts: {yoe:.1f}y exp, skills: {skills_str}, production: {prod_score:.2f}, location: {location}, notice: {notice}d, sem_fit: {sem_score}, exp_quality: {exp_score}
Rules: 1-2 sentences, facts only, no markdown, max 280 chars, mention gap for rank>10.
Output ONLY the reasoning:"""

    try:
        result = await chat(
            prompt,
            system="You are a recruitment AI writing concise, factual candidate assessments.",
            max_tokens=100,
            temperature=0.4,
        )
    except InsufficientCreditsError:
        logger.error("Insufficient credits during generate_candidate_reasoning.")
        return ""

    if result:
        return result[:297] + "..." if len(result) > 300 else result
    return ""
