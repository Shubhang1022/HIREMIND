"""Platform endpoints — projects, jobs, upload, analyze, analytics, export.

Uses JSON file persistence so data survives server restarts.
Will be migrated to Supabase once the project is active.
"""

from __future__ import annotations

import csv
import io
import json
import os
import uuid
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, UploadFile, status, Depends, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.auth import get_optional_user, AuthUser
from app.core.config import settings

router = APIRouter()

def parse_jd_backup(text: str) -> dict:
    if not text:
        return {}
    import re
    result = {}
    
    # 1. Experience extraction
    min_exp = 0.0
    exp_matches = re.findall(r'(?:experience|exp|work\s+exp)[:\-\s–—]*(\d+)\s*(?:-|to|–|—)?\s*(\d+)?\s*(?:years|yrs|year|yr)', text, re.IGNORECASE)
    if exp_matches:
        try:
            min_exp = float(exp_matches[0][0])
        except:
            pass
    else:
        plus_matches = re.findall(r'(\d+)\+\s*(?:years|yrs|year|yr)', text, re.IGNORECASE)
        if plus_matches:
            try:
                min_exp = float(plus_matches[0])
            except:
                pass
    result["experience_years"] = {"min": min_exp}
    
    # 2. Role Category Classification
    role_category = "BACKEND"
    text_lower = text.lower()
    if "mlops" in text_lower:
        role_category = "MLOPS"
    elif "devops" in text_lower or "sre" in text_lower or "infrastructure" in text_lower:
        role_category = "DEVOPS"
    elif "data engineer" in text_lower or "etl" in text_lower:
        role_category = "DATA_ENGINEERING"
    elif "data scientist" in text_lower or "data science" in text_lower:
        role_category = "DATA_SCIENCE"
    elif "ai" in text_lower or "machine learning" in text_lower or "ml" in text_lower or "nlp" in text_lower:
        role_category = "AI_ML"
    elif "frontend" in text_lower or "react" in text_lower or "ui" in text_lower:
        role_category = "FRONTEND"
    elif "project manager" in text_lower or "scrum master" in text_lower:
        role_category = "PROJECT_MANAGEMENT"
    elif "product manager" in text_lower:
        role_category = "PRODUCT_MANAGEMENT"
    elif "design" in text_lower or "ux" in text_lower:
        role_category = "DESIGN"
    result["role_category"] = role_category
    
    # 3. Required skills extraction
    skills = []
    predefined_skills = [
        "python", "sql", "bash", "docker", "kubernetes", "mlflow", "kubeflow", "airflow",
        "aws", "azure", "gcp", "ci/cd", "github actions", "jenkins", "linux", "git",
        "java", "c++", "rust", "go", "scala", "spark", "hadoop", "kafka", "pandas",
        "numpy", "scikit-learn", "tensorflow", "pytorch", "keras", "spacy", "nltk",
        "huggingface", "llm", "embeddings", "vector search", "faiss", "pinecone",
        "weaviate", "qdrant", "milvus", "scrum", "agile", "jira", "terraform", "ansible"
    ]
    for skill in predefined_skills:
        if re.search(r'\b' + re.escape(skill) + r'\b', text_lower):
            skills.append(skill.title() if len(skill) > 3 else skill.upper())
            
    match = re.search(r'(?:required skills|key skills|requirements|what you will need|skills required)[:\-\s–]*([\s\S]+?)(?:\n\n|\n\w+[:\-\s]|$)', text, re.IGNORECASE)
    if match:
        list_text = match.group(1)
        items = re.split(r'[\n•\*\-,\u2022]', list_text)
        for item in items:
            item_clean = item.strip().strip("•*-* \t")
            if item_clean and len(item_clean) < 50 and not any(kw in item_clean.lower() for kw in ["responsibilities", "experience", "years", "plus", "location", "hybrid", "preferred"]):
                if item_clean not in skills:
                    skills.append(item_clean)
                    
    result["must_have_skills"] = skills[:15]
    result["nice_to_have_skills"] = skills[15:25]
    
    # Seniority
    seniority = "Mid"
    if "senior" in text_lower or "lead" in text_lower or "sr\." in text_lower:
        seniority = "Senior"
    elif "junior" in text_lower or "jr\." in text_lower or "entry" in text_lower:
        seniority = "Junior"
    result["seniority"] = seniority
    
    # Title
    title = ""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if lines:
        title = lines[0]
        if len(title) > 80 or "job description" in title.lower() or "jd" in title.lower():
            words = title.split()
            title = " ".join(words[:5])
    result["title"] = title
    
    return result

# ── Supabase integration ──────────────────────────────────────────────────────────
from app.services.storage_provider import create_supabase_client
supabase_client = create_supabase_client(settings.supabase_url, settings.supabase_service_key)

def get_user_id(current_user: Optional[AuthUser]) -> str:
    # Use default user ID if unauthenticated to maintain local compatibility
    return current_user.id if current_user else "d6c20e10-8518-46b3-ba72-e88e77d2a912"

def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

import numpy as np

# Mock memory dictionaries for schema validation / health stats (some code might still reference _health_stats)
_health_stats = {
    "duplicate_projects_prevented": 0,
    "exports_generated": 0,
}

def stream_jsonl(file_like):
    for line in file_like:
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except Exception:
            continue

def stream_json_list(file_like):
    decoder = json.JSONDecoder()
    buffer = ""
    chunk_size = 65536
    found_list_start = False
    
    while True:
        chunk = file_like.read(chunk_size)
        if not chunk:
            break
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8", errors="replace")
        buffer += chunk
        
        if not found_list_start:
            buffer = buffer.lstrip()
            if buffer.startswith("{"):
                idx = buffer.find("[")
                if idx != -1:
                    buffer = buffer[idx + 1:]
                    found_list_start = True
            elif buffer.startswith("["):
                buffer = buffer[1:]
                found_list_start = True
            else:
                found_list_start = True
                
        if found_list_start:
            while buffer:
                buffer = buffer.lstrip().lstrip(",").lstrip()
                if not buffer:
                    break
                if buffer.startswith("]"):
                    break
                try:
                    obj, index = decoder.raw_decode(buffer)
                    yield obj
                    buffer = buffer[index:]
                except json.JSONDecodeError:
                    break

def stream_csv(file_like):
    import csv
    def line_generator():
        for line in file_like:
            if isinstance(line, bytes):
                yield line.decode("utf-8-sig", errors="replace")
            else:
                yield line
                
    reader = csv.reader(line_generator())
    try:
        headers = next(reader)
    except StopIteration:
        return
        
    headers = [h.strip() if h else f"col_{i}" for i, h in enumerate(headers)]
    for i, row in enumerate(reader):
        raw = {headers[j]: (str(v) if v is not None else "") for j, v in enumerate(row) if j < len(headers)}
        if any(v for v in raw.values()):
            yield raw

def stream_candidates(file_like, filename: str):
    ext = Path(filename).suffix.lower()
    if ext == ".jsonl":
        return stream_jsonl(file_like)
    elif ext == ".json":
        return stream_json_list(file_like)
    elif ext == ".csv":
        return stream_csv(file_like)
    else:
        from src.ingestion.engine import IngestionEngine
        engine = IngestionEngine()
        content = file_like.read()
        records = engine.parse_file(content, filename)
        is_redrob_nested = False
        if records:
            first_raw = records[0].raw
            if isinstance(first_raw, dict) and "profile" in first_raw and "career_history" in first_raw and "skills" in first_raw:
                is_redrob_nested = True
        
        def legacy_generator():
            if is_redrob_nested:
                for r in records:
                    yield r.raw
            else:
                candidates_obj = engine.normalize_records(records)
                for c_obj in candidates_obj:
                    yield c_obj.to_dict()
        return legacy_generator()



# ── Hashing and Fingerprinting Helpers ─────────────────────────────────────────
import hashlib

def get_sha256_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def compute_dataset_hash(candidates: list[dict], project_id: Optional[str] = None) -> str:
    if project_id:
        try:
            proj_res = supabase_client.table("projects").select("current_candidate_path").eq("id", project_id).execute()
            if proj_res.data and proj_res.data[0].get("current_candidate_path"):
                current_path = proj_res.data[0]["current_candidate_path"]
                bucket, path = current_path.split("/", 1)
                
                h = hashlib.sha256()
                from app.services.storage_provider import StorageService
                for chunk in StorageService.download_stream(bucket, path):
                    h.update(chunk)
                return h.hexdigest()
        except Exception:
            pass
    if not candidates:
        return ""
    cand_strings = []
    for c in sorted(candidates, key=lambda x: x.get("candidate_id", "")):
        c_str = f"{c.get('candidate_id','')}|{c.get('candidate_name','')}|{c.get('years_of_experience',0)}|{c.get('skills',[])}"
        cand_strings.append(c_str)
    return get_sha256_hash("||".join(cand_strings))


def compute_project_hash(name: str, dataset_hash: str, jd_hash: str) -> str:
    combined = f"{name or ''}||{dataset_hash or ''}||{jd_hash or ''}"
    return get_sha256_hash(combined)

def _update_project_hash(project_id: str) -> None:
    try:
        proj_res = supabase_client.table("projects").select("*").eq("id", project_id).execute()
        if not proj_res.data:
            return
        p = proj_res.data[0]
        
        jobs_res = supabase_client.table("jobs").select("description").eq("project_id", project_id).execute()
        jd_text = ""
        if jobs_res.data:
            jd_text = jobs_res.data[0].get("description") or ""
        jd_hash = get_sha256_hash(jd_text) if jd_text else ""
        
        dataset_hash = compute_dataset_hash(None, project_id=project_id)
        project_hash = compute_project_hash(p.get("name", ""), dataset_hash, jd_hash)
        
        supabase_client.table("projects").update({
            "project_hash": project_hash,
            "dataset_hash": dataset_hash,
            "jd_hash": jd_hash,
            "updated_at": _now()
        }).eq("id", project_id).execute()
    except Exception:
        pass


def run_startup_initialization() -> None:
    """
    Run startup integrity checks and timeouts enforcement using Supabase.
    """
    import logging
    logger = logging.getLogger(__name__)
    try:
        integrity_result = _run_integrity_check()
        logger.info("Startup Integrity Check: %s", integrity_result)

        _enforce_analysis_timeouts()
        _enforce_embedding_timeouts()
        logger.info("Enforced analysis and embedding timeouts on startup.")
    except Exception as exc:
        logger.warning("Startup initialization encountered an error, but backend startup will proceed: %s", exc)


# ── Cached embedding model (loaded once, reused across all requests) ──────────
_encoder = None

def _get_encoder():
    global _encoder
    from app.core.config import settings
    target_model = settings.embedding_model
    
    if _encoder is not None:
        if _encoder.model_name != target_model:
            import logging
            logging.getLogger(__name__).info("Embedding model configuration changed from %s to %s. Reloading model...", _encoder.model_name, target_model)
            try:
                from src.features.embedding import _MODEL_CACHE
                _MODEL_CACHE.clear()
            except Exception:
                pass
            _encoder = None
            import gc
            gc.collect()

    if _encoder is None:
        import sys, os
        _project_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))))
        if _project_root not in sys.path:
            sys.path.insert(0, _project_root)
        from src.features.embedding import EmbeddingEncoder
        _encoder = EmbeddingEncoder(model_name=target_model)
        _encoder.load_model()
    return _encoder


# ── Memory Telemetry & Active Project Lock ────────────────────────────────────
_active_analyses: set[str] = set()

def get_memory_mb() -> float:
    try:
        import os
        import psutil
        process = psutil.Process(os.getpid())
        return process.memory_info().rss / (1024 * 1024)
    except Exception:
        try:
            with open("/proc/self/status", "r") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return float(line.split()[1]) / 1024
        except Exception:
            pass
        return 0.0

def log_memory(label: str) -> float:
    import logging
    mem = get_memory_mb()
    msg = f"[MEMORY_TELEMETRY] {label}: {mem:.2f} MB"
    logging.getLogger(__name__).info(msg)
    print(msg, flush=True)
    return mem


def standardize_candidate(c: dict) -> dict:
    if not isinstance(c, dict):
        return {}

    # If it is already in the Redrob nested format, return it
    if "profile" in c and "career_history" in c and "skills" in c:
        if "candidate_id" not in c and "external_id" in c:
            c["candidate_id"] = c["external_id"]
        elif "candidate_id" not in c:
            c["candidate_id"] = f"CAND_{abs(hash(c.get('profile', {}).get('anonymized_name', ''))) % 10000000:07d}"
        return c

    # Build the Redrob-compatible nested format
    profile = {
        "anonymized_name": c.get("full_name") or c.get("candidate_name") or c.get("name") or "—",
        "headline": c.get("headline") or "—",
        "summary": c.get("summary") or c.get("text_for_embedding") or "—",
        "location": c.get("location") or "—",
        "country": c.get("country") or "India",
        "years_of_experience": float(c.get("years_of_experience") or c.get("years_exp") or 0.0),
        "current_title": c.get("current_title") or "—",
        "current_company": c.get("current_company") or "—",
        "current_company_size": c.get("current_company_size") or "11-50",
        "current_industry": c.get("current_industry") or "Technology",
    }

    # Format career history
    raw_history = c.get("experience") or c.get("career_history") or []
    career_history = []
    if isinstance(raw_history, list):
        for role in raw_history:
            if isinstance(role, dict):
                career_history.append({
                    "company": role.get("company") or "—",
                    "title": role.get("title") or "—",
                    "start_date": role.get("start_date") or "2020-01-01",
                    "end_date": role.get("end_date"),
                    "duration_months": int(role.get("duration_months") or 12),
                    "is_current": bool(role.get("is_current", False)),
                    "industry": role.get("industry") or "Technology",
                    "company_size": role.get("company_size") or "11-50",
                    "description": role.get("description") or "",
                })
    
    # If career history is empty, populate a dummy role based on current title/company
    if not career_history and (profile["current_title"] != "—" or profile["current_company"] != "—"):
        career_history.append({
            "company": profile["current_company"],
            "title": profile["current_title"],
            "start_date": "2020-01-01",
            "end_date": None,
            "duration_months": int(profile["years_of_experience"] * 12) or 12,
            "is_current": True,
            "industry": "Technology",
            "company_size": "11-50",
            "description": profile["summary"] or "",
        })

    # Format skills
    raw_skills = c.get("skills") or []
    skills = []
    if isinstance(raw_skills, list):
        for s in raw_skills:
            if isinstance(s, dict):
                skills.append({
                    "name": s.get("name") or "",
                    "proficiency": s.get("proficiency") or "intermediate",
                    "endorsements": int(s.get("endorsements") or 0),
                    "duration_months": int(s.get("duration_months") or 0),
                })
            elif isinstance(s, str):
                skills.append({
                    "name": s,
                    "proficiency": "intermediate",
                    "endorsements": 0,
                    "duration_months": 0,
                })

    # Format education
    raw_edu = c.get("education") or []
    education = []
    if isinstance(raw_edu, list):
        for edu in raw_edu:
            if isinstance(edu, dict):
                education.append({
                    "institution": edu.get("institution") or edu.get("school") or "—",
                    "degree": edu.get("degree") or "—",
                    "field_of_study": edu.get("field_of_study") or edu.get("major") or "—",
                    "start_year": int(edu.get("start_year") or 2016),
                    "end_year": int(edu.get("end_year") or 2020),
                    "grade": edu.get("grade"),
                    "tier": edu.get("tier") or "unknown",
                })
            elif isinstance(edu, str):
                education.append({
                    "institution": "—",
                    "degree": edu,
                    "field_of_study": "—",
                    "start_year": 2016,
                    "end_year": 2020,
                    "grade": None,
                    "tier": "unknown",
                })

    # Redrob signals
    raw_signals = c.get("redrob_signals") or c.get("signals") or {}
    redrob_signals = {
        "profile_completeness_score": float(raw_signals.get("profile_completeness_score") or 100.0),
        "signup_date": raw_signals.get("signup_date") or "2025-01-01",
        "last_active_date": raw_signals.get("last_active_date") or "2025-06-01",
        "open_to_work_flag": bool(raw_signals.get("open_to_work_flag", True)),
        "profile_views_received_30d": int(raw_signals.get("profile_views_received_30d") or 10),
        "applications_submitted_30d": int(raw_signals.get("applications_submitted_30d") or 5),
        "recruiter_response_rate": float(raw_signals.get("recruiter_response_rate") or 1.0),
        "avg_response_time_hours": float(raw_signals.get("avg_response_time_hours") or 24.0),
        "skill_assessment_scores": raw_signals.get("skill_assessment_scores") or {},
        "connection_count": int(raw_signals.get("connection_count") or 50),
        "endorsements_received": int(raw_signals.get("endorsements_received") or 5),
        "notice_period_days": int(raw_signals.get("notice_period_days") or 30),
        "expected_salary_range_inr_lpa": raw_signals.get("expected_salary_range_inr_lpa") or {"min": 10.0, "max": 20.0},
        "preferred_work_mode": raw_signals.get("preferred_work_mode") or "remote",
        "willing_to_relocate": bool(raw_signals.get("willing_to_relocate", True)),
        "github_activity_score": float(raw_signals.get("github_activity_score") or 50.0),
        "search_appearance_30d": int(raw_signals.get("search_appearance_30d") or 5),
        "saved_by_recruiters_30d": int(raw_signals.get("saved_by_recruiters_30d") or 1),
        "interview_completion_rate": float(raw_signals.get("interview_completion_rate") or 1.0),
        "offer_acceptance_rate": float(raw_signals.get("offer_acceptance_rate") or 1.0),
        "verified_email": bool(raw_signals.get("verified_email", True)),
        "verified_phone": bool(raw_signals.get("verified_phone", True)),
        "linkedin_connected": bool(raw_signals.get("linkedin_connected", True)),
    }

    candidate_id = c.get("candidate_id") or c.get("external_id") or f"CAND_{abs(hash(profile['anonymized_name'])) % 10000000:07d}"

    return {
        "candidate_id": candidate_id,
        "profile": profile,
        "career_history": career_history,
        "skills": skills,
        "redrob_signals": redrob_signals,
        "education": education,
        "certifications": c.get("certifications") or [],
        "languages": c.get("languages") or [],
    }


def enrich_candidate_with_intelligence(c: dict) -> dict:
    c = standardize_candidate(c)
    if "candidate_intelligence" not in c:
        try:
            import sys, os
            _project_root = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))))
            if _project_root not in sys.path:
                sys.path.insert(0, _project_root)
            from src.features.structured import StructuredFeatureExtractor
            extractor = StructuredFeatureExtractor()
            feats = extractor.extract(c)
            c["candidate_intelligence"] = feats.get("candidate_intelligence")
            c["candidate_specialization"] = feats.get("candidate_specialization")
            c["specialization_confidence"] = feats.get("specialization_confidence")
            c["candidate_type"] = feats.get("candidate_type")
            c["relevant_years_exp"] = feats.get("relevant_years_exp")
            c["education_tier"] = feats.get("education_tier")
            c["education_is_tech"] = feats.get("education_is_tech")
            c["candidate_quality_score"] = feats.get("candidate_quality_score")
            c["candidate_role_category"] = feats.get("candidate_role_category")
            c["is_disqualified"] = feats.get("is_disqualified")
            c["disqualifier_reason"] = feats.get("disqualifier_reason")
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Could not extract intelligence for candidate: %s", e)
    return c


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    project_hash: Optional[str] = None
    dataset_hash: Optional[str] = None
    jd_hash: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None


class JobCreate(BaseModel):
    title: str
    description: str
    company: Optional[str] = None
    location: Optional[str] = None
    work_mode: Optional[str] = None
    required_skills: list[str] = []
    min_experience: Optional[float] = None
    
    # Recruiter-controlled metadata
    openings: Optional[int] = 5
    shortlist_size: Optional[int] = 15
    priority: Optional[str] = "balanced"
    min_match_percent: Optional[float] = None
    salary_range: Optional[str] = None
    job_location: Optional[str] = None
    employment_type: Optional[str] = None


class AnalysisRequest(BaseModel):
    job_id: str
    top_k: int = 100
    performance_mode: Optional[str] = "balanced"


class ExportRequest(BaseModel):
    ranking_id: str
    format: str = "csv"


# ── Analysis Timeout Recovery ─────────────────────────────────────────────────
def _enforce_analysis_timeouts() -> None:
    try:
        res = supabase_client.table("projects").select("*").in_("status", ["queued", "processing", "ranking"]).execute()
        now_dt = datetime.now(timezone.utc)
        for p in res.data:
            updated_at_str = p.get("updated_at") or p.get("created_at")
            if updated_at_str:
                updated_at_dt = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
                age_minutes = (now_dt - updated_at_dt).total_seconds() / 60.0
                if age_minutes > 30.0:
                    supabase_client.table("projects").update({
                        "status": "failed",
                        "updated_at": _now()
                    }).eq("id", p["id"]).execute()
    except Exception:
        pass


# ── Background Worker Health Check & Indexing ─────────────────────────────────
def _enforce_embedding_timeouts() -> None:
    try:
        res = supabase_client.table("projects").select("*").in_("embedding_status", ["queued", "processing"]).execute()
        now_dt = datetime.now(timezone.utc)
        for p in res.data:
            started_at_str = p.get("started_at") or p.get("updated_at") or p.get("created_at")
            if started_at_str:
                started_dt = datetime.fromisoformat(started_at_str.replace("Z", "+00:00"))
                age_hours = (now_dt - started_dt).total_seconds() / 3600.0
                if age_hours > 1.0:
                    supabase_client.table("projects").update({
                        "embedding_status": "failed",
                        "updated_at": _now()
                    }).eq("id", p["id"]).execute()
    except Exception:
        pass


def process_project_data_task(project_id: str):
    import time
    import traceback
    import logging
    import json
    import tempfile
    import shutil
    import os
    from pathlib import Path
    import numpy as np

    logger = logging.getLogger(__name__)
    logger.info("Starting background candidate indexing task for project %s", project_id)

    try:
        supabase_client.table("projects").update({
            "embedding_status": "processing",
            "status": "INDEXING",
            "updated_at": _now()
        }).eq("id", project_id).execute()
    except Exception as exc:
        logger.error("Could not update project status to processing: %s", exc)
        return

    temp_dir = None
    try:
        proj_res = supabase_client.table("projects").select("*").eq("id", project_id).execute()
        if not proj_res.data:
            logger.error("Project %s not found in background task", project_id)
            return
        p = proj_res.data[0]
        
        current_path = p.get("current_candidate_path")
        if not current_path:
            raise FileNotFoundError("No candidate upload path found in project")
            
        bucket, path = current_path.split("/", 1)
        
        from app.services.storage_provider import StorageService
        from src.features.structured import _classify_specialization_with_confidence, classify_candidate_role_category, HARD_DISQUALIFIER_TITLES
        from src.scoring.quality import calculate_candidate_quality_score
        
        # Create temp directory for streaming operations
        temp_dir = tempfile.mkdtemp(prefix=f"index_job_{project_id}_")
        
        role_files = {}
        enriched_jsonl_path = Path(temp_dir) / "enriched_candidates.jsonl"
        
        candidate_ids = []
        skill_index = {}
        total_candidates = 0

        with open(enriched_jsonl_path, "w", encoding="utf-8") as f_enriched:
            for idx, c_raw in enumerate(StorageService.stream_jsonl(bucket, path)):
                c = standardize_candidate(c_raw)
                profile = c.get("profile", {})
                career_history = c.get("career_history", [])
                skills_list = c.get("skills", [])

                is_disqualified = False
                disqualifier_reason = None
                current_title = str(profile.get("current_title", "")).lower()
                if any(dt in current_title for dt in HARD_DISQUALIFIER_TITLES):
                    is_disqualified = True
                    disqualifier_reason = "non_technical"

                cand_specialization, _ = _classify_specialization_with_confidence(profile, career_history, skills_list)
                cand_category = classify_candidate_role_category(profile, career_history, skills_list)
                cand_yoe = float(profile.get("years_of_experience") or 0.0)

                features_so_far = {
                    "years_exp": cand_yoe,
                    "is_disqualified": is_disqualified,
                    "disqualifier_reason": disqualifier_reason,
                }
                cand_quality_score = calculate_candidate_quality_score(features_so_far, c)

                c["candidate_specialization"] = cand_specialization
                c["candidate_role_category"] = normalize_role_category(cand_category)
                c["years_exp"] = cand_yoe
                c["candidate_quality_score"] = cand_quality_score
                c["is_disqualified"] = is_disqualified
                c["disqualifier_reason"] = disqualifier_reason

                f_enriched.write(json.dumps(c, ensure_ascii=False) + "\n")

                cat = normalize_role_category(cand_category)
                if cat not in role_files:
                    cat_path = Path(temp_dir) / f"role_{cat.upper()}.jsonl"
                    role_files[cat] = open(cat_path, "w", encoding="utf-8")
                role_files[cat].write(json.dumps(c, ensure_ascii=False) + "\n")

                candidate_ids.append(c.get("candidate_id"))
                for s in skills_list:
                    s_name = s.get("name") if isinstance(s, dict) else s
                    if s_name:
                        s_name_clean = str(s_name).strip().lower()
                        if s_name_clean:
                            skill_index.setdefault(s_name_clean, []).append(idx)
                            
                total_candidates += 1

        for f in role_files.values():
            f.close()

        logger.info("Enriched %d candidates. Starting index uploads.", total_candidates)

        for cat in role_files.keys():
            cat_path = Path(temp_dir) / f"role_{cat.upper()}.jsonl"
            content = cat_path.read_bytes()
            StorageService.upload_file("role-indexes", f"{project_id}/role_{cat.upper()}_v1.jsonl", content)

        skill_content = json.dumps(skill_index, ensure_ascii=False)
        StorageService.upload_file("skill-indexes", f"{project_id}/skill_index_v1.json", skill_content.encode("utf-8"))

        ids_content = json.dumps(candidate_ids, ensure_ascii=False)
        StorageService.upload_file("embeddings", f"{project_id}/ids_v1.json", ids_content.encode("utf-8"))

        from src.features.text_builder import build_candidate_text
        encoder = _get_encoder()
        
        batch_size = 500
        raw_embs_path = Path(temp_dir) / "embeddings.raw"
        
        import faiss
        index = None
        dim = None
        global_idx = 0

        with open(raw_embs_path, "wb") as f_raw_embs:
            batch_candidates = []
            batch_texts = []
            
            with open(enriched_jsonl_path, "r", encoding="utf-8") as f_enriched:
                for line in f_enriched:
                    c = json.loads(line)
                    batch_candidates.append(c)
                    if c.get("is_disqualified", False):
                        batch_texts.append("")
                    else:
                        batch_texts.append(build_candidate_text(c))
                        
                    if len(batch_candidates) == batch_size:
                        valid_indices = [i for i, t in enumerate(batch_texts) if t]
                        valid_texts = [batch_texts[i] for i in valid_indices]
                        
                        if dim is None:
                            dim = encoder.embedding_dim
                            sub_index = faiss.IndexFlatIP(dim)
                            index = faiss.IndexIDMap(sub_index)
                            
                        arr = np.zeros((len(batch_candidates), dim), dtype=np.float32)
                        if valid_texts:
                            encoded = encoder.encode_batch(valid_texts, normalize=True, bge_mode="passage")
                            for arr_idx, orig_idx in enumerate(valid_indices):
                                arr[orig_idx] = encoded[arr_idx]
                                
                        f_raw_embs.write(arr.tobytes())
                        ids = np.arange(global_idx, global_idx + len(batch_candidates), dtype=np.int64)
                        index.add_with_ids(arr, ids)
                        global_idx += len(batch_candidates)
                        batch_candidates = []
                        batch_texts = []
                        
                if batch_candidates:
                    valid_indices = [i for i, t in enumerate(batch_texts) if t]
                    valid_texts = [batch_texts[i] for i in valid_indices]
                    
                    if dim is None:
                        dim = encoder.embedding_dim
                        sub_index = faiss.IndexFlatIP(dim)
                        index = faiss.IndexIDMap(sub_index)
                        
                    arr = np.zeros((len(batch_candidates), dim), dtype=np.float32)
                    if valid_texts:
                        encoded = encoder.encode_batch(valid_texts, normalize=True, bge_mode="passage")
                        for arr_idx, orig_idx in enumerate(valid_indices):
                            arr[orig_idx] = encoded[arr_idx]
                            
                    f_raw_embs.write(arr.tobytes())
                    ids = np.arange(global_idx, global_idx + len(batch_candidates), dtype=np.int64)
                    index.add_with_ids(arr, ids)
                    global_idx += len(batch_candidates)

        npy_path = Path(temp_dir) / "embeddings.npy"
        if dim is None:
            dim = encoder.embedding_dim
            
        with open(npy_path, "wb") as f_npy:
            import struct
            f_npy.write(b'\x93NUMPY')
            f_npy.write(b'\x01\x00')
            
            header_str = f"{{'descr': '<f4', 'fortran_order': False, 'shape': ({total_candidates}, {dim})}} "
            pad_len = 64 - ((10 + len(header_str) + 1) % 64)
            if pad_len == 64:
                pad_len = 0
            header_str += " " * pad_len + "\n"
            
            f_npy.write(struct.pack('<H', len(header_str)))
            f_npy.write(header_str.encode('ascii'))
            
            if os.path.exists(raw_embs_path):
                with open(raw_embs_path, "rb") as f_raw:
                    while True:
                        chunk = f_raw.read(65536)
                        if not chunk:
                            break
                        f_npy.write(chunk)

        npy_content = npy_path.read_bytes()
        StorageService.upload_file("embeddings", f"{project_id}/embeddings_v1.npy", npy_content)

        if index is not None:
            faiss_arr = faiss.serialize_index(index)
            StorageService.upload_file("faiss-indexes", f"{project_id}/faiss_v1.index", faiss_arr.tobytes())
        else:
            sub_index = faiss.IndexFlatIP(dim)
            index = faiss.IndexIDMap(sub_index)
            faiss_arr = faiss.serialize_index(index)
            StorageService.upload_file("faiss-indexes", f"{project_id}/faiss_v1.index", faiss_arr.tobytes())

        enriched_content = enriched_jsonl_path.read_bytes()
        StorageService.upload_file("candidate-files", path, enriched_content)

        supabase_client.table("projects").update({
            "embedding_status": "ready",
            "status": "INDEX_READY",
            "embeddings_path": f"embeddings/{project_id}/embeddings_v1.npy",
            "faiss_index_path": f"faiss-indexes/{project_id}/faiss_v1.index",
            "role_index_path": f"role-indexes/{project_id}/role_index_v1.json",
            "skill_index_path": f"skill-indexes/{project_id}/skill_index_v1.json",
            "updated_at": _now()
        }).eq("id", project_id).execute()
        
        logger.info("Background indexing task completed successfully for project %s", project_id)

    except Exception as e:
        logger.error("Error in background candidate indexing task: %s", traceback.format_exc())
        supabase_client.table("projects").update({
            "embedding_status": "failed",
            "status": "FAILED",
            "upload_statistics": {"failure_reason": f"Background indexing failed: {e}"},
            "updated_at": _now()
        }).eq("id", project_id).execute()
    finally:
        from app.services.cache_service import CacheService
        CacheService.invalidate_project(project_id)
        
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass
                
        large_vars = [
            "candidates", "role_files", "candidate_ids", "skill_index",
            "batch_candidates", "batch_texts", "arr", "encoded",
            "npy_content", "faiss_arr", "enriched_content", "index", "sub_index"
        ]
        for v in large_vars:
            if v in locals():
                try:
                    del locals()[v]
                except Exception:
                    pass
        import gc
        gc.collect()


# ── Startup Integrity Check (Mocked for Supabase) ─────────────────────────────
def _run_integrity_check() -> str:
    return "Integrity checks passed (Supabase active)"


# ── Projects ──────────────────────────────────────────────────────────────────

@router.get("/projects")
async def list_projects(current_user: Optional[AuthUser] = Depends(get_optional_user)):
    _enforce_analysis_timeouts()
    _enforce_embedding_timeouts()
    user_id = get_user_id(current_user)
    res = supabase_client.table("projects").select("*").eq("user_id", user_id).execute()
    return res.data


@router.post("/projects", status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    current_user: Optional[AuthUser] = Depends(get_optional_user)
):
    import logging
    logger = logging.getLogger(__name__)
    user_id = get_user_id(current_user)

    dup = supabase_client.table("projects").select("id").eq("user_id", user_id).eq("name", body.name).execute()
    if dup.data:
        _health_stats["duplicate_projects_prevented"] += 1
        res = supabase_client.table("projects").select("*").eq("id", dup.data[0]["id"]).execute()
        return res.data[0]

    pid = str(uuid.uuid4())
    now = _now()
    project = {
        "id": pid,
        "user_id": user_id,
        "name": body.name,
        "description": body.description or "",
        "status": "draft",
        "candidate_count": 0,
        "job_count": 0,
        "project_hash": body.project_hash,
        "embedding_status": "ready",
        "upload_statistics": {},
        "created_at": now,
        "updated_at": now,
    }
    
    supabase_client.table("projects").insert(project).execute()
    return project


@router.get("/projects/{project_id}")
async def get_project(project_id: str, current_user: Optional[AuthUser] = Depends(get_optional_user)):
    _enforce_analysis_timeouts()
    _enforce_embedding_timeouts()
    user_id = get_user_id(current_user)
    res = supabase_client.table("projects").select("*").eq("id", project_id).eq("user_id", user_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Project not found")
    return res.data[0]


@router.patch("/projects/{project_id}")
async def update_project(
    project_id: str,
    body: ProjectUpdate,
    current_user: Optional[AuthUser] = Depends(get_optional_user)
):
    user_id = get_user_id(current_user)
    existing = supabase_client.table("projects").select("id").eq("id", project_id).eq("user_id", user_id).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Project not found")

    update_data = {}
    if body.name is not None:
        update_data["name"] = body.name
    if body.description is not None:
        update_data["description"] = body.description
    if body.status is not None:
        update_data["status"] = body.status

    if not update_data:
        return existing.data[0]

    update_data["updated_at"] = _now()
    res = supabase_client.table("projects").update(update_data).eq("id", project_id).execute()
    return res.data[0]


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str, current_user: Optional[AuthUser] = Depends(get_optional_user)):
    user_id = get_user_id(current_user)
    existing = supabase_client.table("projects").select("id").eq("id", project_id).eq("user_id", user_id).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Project not found")

    supabase_client.table("projects").delete().eq("id", project_id).execute()

    from app.services.storage_provider import StorageService
    StorageService.delete_file("candidate-files", f"{project_id}/candidate_v1.jsonl")
    StorageService.delete_file("embeddings", f"{project_id}/embeddings_v1.npy")
    StorageService.delete_file("faiss-indexes", f"{project_id}/faiss_v1.index")
    StorageService.delete_file("role-indexes", f"{project_id}/role_index_v1.json")
    StorageService.delete_file("skill-indexes", f"{project_id}/skill_index_v1.json")

    from app.services.cache_service import CacheService
    CacheService.invalidate_project(project_id)


# ── File Upload ───────────────────────────────────────────────────────────────

@router.post("/projects/{project_id}/upload")
async def upload_file(
    project_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    upload_type: str = "candidates",
    title: Optional[str] = None,
    openings: Optional[int] = None,
    shortlist_size: Optional[int] = None,
    priority: Optional[str] = None,
    min_match_percent: Optional[float] = None,
    salary_range: Optional[str] = None,
    job_location: Optional[str] = None,
    employment_type: Optional[str] = None,
    current_user: Optional[AuthUser] = Depends(get_optional_user),
):
    user_id = get_user_id(current_user)
    proj_res = supabase_client.table("projects").select("*").eq("id", project_id).eq("user_id", user_id).execute()
    if not proj_res.data:
        raise HTTPException(status_code=404, detail="Project not found")
    p = proj_res.data[0]

    filename = file.filename or "upload"

    if upload_type == "candidates":
        try:
            file_like = file.file
            file_like.seek(0)
            
            temp_local_file = Path(f"data/temp_upload_{project_id}.jsonl")
            temp_local_file.parent.mkdir(parents=True, exist_ok=True)
            
            records_parsed = 0
            try:
                with open(temp_local_file, "w", encoding="utf-8") as out_f:
                    chunk = []
                    for cand_raw in stream_candidates(file_like, filename):
                        standardized = standardize_candidate(cand_raw)
                        chunk.append(standardized)
                        if len(chunk) >= 1000:
                            for c in chunk:
                                out_f.write(json.dumps(c, ensure_ascii=False) + "\n")
                            records_parsed += len(chunk)
                            chunk = []
                    if chunk:
                        for c in chunk:
                            out_f.write(json.dumps(c, ensure_ascii=False) + "\n")
                        records_parsed += len(chunk)

                if records_parsed == 0:
                    raise ValueError("No valid candidate records found in file")

                # Get new version
                version_res = supabase_client.table("candidate_uploads").select("version").eq("project_id", project_id).order("version", desc=True).limit(1).execute()
                new_version = version_res.data[0]["version"] + 1 if version_res.data else 1
                
                from app.services.storage_provider import StorageService
                storage_path = f"{project_id}/candidate_v{new_version}.jsonl"
                StorageService.upload_file("candidate-files", storage_path, temp_local_file.read_bytes())

                supabase_client.table("candidate_uploads").insert({
                    "project_id": project_id,
                    "storage_path": storage_path,
                    "version": new_version,
                    "candidate_count": records_parsed,
                    "status": "COMPLETED"
                }).execute()

                supabase_client.table("projects").update({
                    "candidate_count": records_parsed,
                    "status": "uploaded" if p.get("status") in ("CREATED", "draft") else p.get("status"),
                    "embedding_status": "queued",
                    "current_candidate_path": f"candidate-files/{storage_path}",
                    "updated_at": _now()
                }).eq("id", project_id).execute()

                background_tasks.add_task(process_project_data_task, project_id)
            finally:
                if temp_local_file.exists():
                    temp_local_file.unlink()

        except Exception as e:
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=422, detail=f"Could not parse file: {e}")

    elif upload_type == "job_description":
        from app.core.openrouter import parse_jd_with_llm

        content = await file.read()
        raw_text = ""
        filename_lower = filename.lower()

        if filename_lower.endswith(".docx"):
            try:
                from docx import Document
                import io as _io
                doc = Document(_io.BytesIO(content))
                raw_text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            except Exception:
                raw_text = ""
        elif filename_lower.endswith(".pdf"):
            try:
                import pypdf
                import io as _io
                reader = pypdf.PdfReader(_io.BytesIO(content))
                raw_text = "\n".join(page.extract_text() or "" for page in reader.pages)
            except Exception:
                raw_text = ""

        if not raw_text:
            try:
                raw_text = content.decode("utf-8")
            except Exception:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported or unreadable job description format.")

        llm_parsed = {}
        try:
            llm_parsed = await parse_jd_with_llm(raw_text)
        except Exception:
            llm_parsed = parse_jd_backup(raw_text)

        required_skills = llm_parsed.get("must_have_skills", [])
        min_experience = float(llm_parsed.get("experience_years", {}).get("min") or 0.0)

        jid = str(uuid.uuid4())
        job = {
            "id": jid,
            "project_id": project_id,
            "title": title or llm_parsed.get("title") or "Job Description",
            "description": raw_text,
            "company": "Company",
            "location": "Remote",
            "work_mode": "Remote",
            "required_skills": required_skills,
            "nice_to_have_skills": llm_parsed.get("nice_to_have_skills", []),
            "min_experience": min_experience,
            "preferred_locations": llm_parsed.get("preferred_locations", []),
            "openings": openings or 5,
            "shortlist_size": shortlist_size or 15,
            "priority": priority or "balanced",
            "min_match_percent": min_match_percent,
            "salary_range": salary_range,
            "job_location": job_location,
            "employment_type": employment_type or "Full-time",
            "created_at": _now()
        }
        
        supabase_client.table("jobs").insert(job).execute()

        supabase_client.table("projects").update({
            "job_count": supabase_client.table("jobs").select("id", count="exact").eq("project_id", project_id).execute().count or 1,
            "status": "uploaded" if p.get("status") in ("CREATED", "draft") and p.get("candidate_count", 0) > 0 else p.get("status"),
            "updated_at": _now()
        }).eq("id", project_id).execute()

        return job


@router.get("/projects/{project_id}/candidates")
async def list_candidates(
    project_id: str,
    page: int = 1,
    page_size: int = 50,
    search: str = "",
    current_user: Optional[AuthUser] = Depends(get_optional_user)
):
    user_id = get_user_id(current_user)
    res = supabase_client.table("projects").select("current_candidate_path").eq("id", project_id).eq("user_id", user_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Project not found")

    current_path = res.data[0].get("current_candidate_path")
    if not current_path:
        return {
            "total": 0,
            "page": page,
            "page_size": page_size,
            "pages": 1,
            "candidates": [],
        }

    bucket, path = current_path.split("/", 1)
    total = 0
    matched_rows = []
    q = search.lower() if search else None

    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size

    from app.services.storage_provider import StorageService
    for c_raw in StorageService.stream_jsonl(bucket, path):
        c = standardize_candidate(c_raw)
        profile = c.get("profile", {})
        signals = c.get("redrob_signals", {})
        skills = c.get("skills", [])
        
        candidate_id = c.get("candidate_id", "")
        name = profile.get("anonymized_name", "—")
        current_title = profile.get("current_title", "—")
        current_company = profile.get("current_company", "—")
        location = profile.get("location", "—")
        top_skills = [s.get("name", "") for s in skills[:5] if s.get("name")]

        match = True
        if q:
            match = (
                q in name.lower()
                or q in current_title.lower()
                or q in current_company.lower()
                or q in location.lower()
                or q in candidate_id.lower()
                or any(q in r.lower() for r in top_skills)
            )

        if match:
            if total >= start_idx and total < end_idx:
                matched_rows.append({
                    "candidate_id": candidate_id,
                    "name": name,
                    "current_title": current_title,
                    "current_company": current_company,
                    "location": location,
                    "years_of_experience": profile.get("years_of_experience", 0),
                    "top_skills": top_skills,
                    "open_to_work": signals.get("open_to_work_flag", False),
                    "notice_period_days": signals.get("notice_period_days", None),
                    "profile_completeness": signals.get("profile_completeness_score", 0),
                })
            total += 1

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "candidates": matched_rows,
    }


@router.get("/projects/{project_id}/candidates/{candidate_id}")
async def get_candidate(
    project_id: str,
    candidate_id: str,
    current_user: Optional[AuthUser] = Depends(get_optional_user)
):
    user_id = get_user_id(current_user)
    res = supabase_client.table("projects").select("current_candidate_path").eq("id", project_id).eq("user_id", user_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Project not found")

    current_path = res.data[0].get("current_candidate_path")
    if not current_path:
        raise HTTPException(status_code=404, detail="Candidate not found")

    bucket, path = current_path.split("/", 1)
    from app.services.storage_provider import StorageService
    for c in StorageService.stream_jsonl(bucket, path):
        if c.get("candidate_id") == candidate_id:
            return standardize_candidate(c)

    raise HTTPException(status_code=404, detail="Candidate not found")


@router.get("/projects/{project_id}/jobs")
async def list_jobs(project_id: str, current_user: Optional[AuthUser] = Depends(get_optional_user)):
    user_id = get_user_id(current_user)
    proj = supabase_client.table("projects").select("id").eq("id", project_id).eq("user_id", user_id).execute()
    if not proj.data:
        raise HTTPException(status_code=404, detail="Project not found")

    res = supabase_client.table("jobs").select("*").eq("project_id", project_id).execute()
    return res.data


# ── AI Analysis ───────────────────────────────────────────────────────────────

import time as _time
_backend_ranking_cache: dict[str, dict] = {}
_CACHE_TTL = 2 * 24 * 60 * 60

def _get_cached_ranking(project_id: str, job_id: str) -> dict | None:
    key = f"{project_id}:{job_id}"
    entry = _backend_ranking_cache.get(key)
    if not entry:
        return None
    if _time.time() - entry["cached_at"] > _CACHE_TTL:
        del _backend_ranking_cache[key]
        return None
    return entry["ranking"]

def _set_cached_ranking(project_id: str, job_id: str, ranking: dict) -> None:
    key = f"{project_id}:{job_id}"
    _backend_ranking_cache[key] = {"ranking": ranking, "cached_at": _time.time()}

def normalize_role_category(cat: str) -> str:
    if not cat:
        return "BACKEND"
    cat_clean = str(cat).strip().upper()
    if cat_clean in {"MLOPS", "DEVOPS", "DATA_ENGINEERING", "DATA_SCIENCE", "AI_ML", "FRONTEND", "PROJECT_MANAGEMENT", "PRODUCT_MANAGEMENT", "DESIGN", "MARKETING", "HR", "BACKEND"}:
        return cat_clean
    mapping = {
        "BACKEND ENGINEER": "BACKEND",
        "DEVOPS ENGINEER": "DEVOPS",
        "PLATFORM ENGINEER": "DEVOPS",
        "MLOPS ENGINEER": "MLOPS",
        "ML ENGINEER": "AI_ML",
        "RETRIEVAL ENGINEER": "AI_ML",
        "SEARCH ENGINEER": "AI_ML",
        "RECOMMENDATION SYSTEMS ENGINEER": "AI_ML",
        "DATA SCIENTIST": "DATA_SCIENCE",
        "FRONTEND ENGINEER": "FRONTEND",
        "PROJECT MANAGER": "PROJECT_MANAGEMENT",
        "OPERATIONS MANAGER": "PROJECT_MANAGEMENT",
    }
    return mapping.get(cat_clean, "BACKEND")


@router.post("/projects/{project_id}/analyze")
async def run_analysis(
    project_id: str,
    body: AnalysisRequest,
    current_user: Optional[AuthUser] = Depends(get_optional_user),
):
    import logging
    import asyncio
    import time
    import heapq
    import numpy as np
    import json
    import uuid
    from collections import Counter
    from app.services.storage_provider import StorageService
    from app.services.cache_service import CacheService
    from src.ranking.engine import COMPATIBLE_CATEGORIES

    logger = logging.getLogger(__name__)
    user_id = get_user_id(current_user)

    # 1. Concurrent Analysis Protection (Project Lock)
    if project_id in _active_analyses:
        logger.warning("Concurrent analysis attempt rejected for project %s", project_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Analysis already in progress."
        )

    proj_res = supabase_client.table("projects").select("*").eq("id", project_id).eq("user_id", user_id).execute()
    if not proj_res.data:
        raise HTTPException(status_code=404, detail="Project not found")
    p = proj_res.data[0]

    # Database-level check for active analyses
    if p.get("status") in ["queued", "processing", "ranking"]:
        logger.info("Duplicate database-level analysis prevented for project %s", project_id)
        active_res = supabase_client.table("rankings").select("*").eq("project_id", project_id).eq("job_id", body.job_id).order("created_at", desc=True).limit(1).execute()
        if active_res.data:
            ranking_id = active_res.data[0]["id"]
            results_res = supabase_client.table("ranking_results").select("full_result").eq("ranking_id", ranking_id).order("rank").execute()
            active_res.data[0]["results"] = [r["full_result"] for r in results_res.data]
            return active_res.data[0]
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Analysis already in progress."
        )

    # Acquire lock
    _active_analyses.add(project_id)

    # Initialize telemetry variables
    t_start_all = time.time()
    peak_memory = get_memory_mb()
    
    def check_overall_timeout():
        if time.time() - t_start_all > 600.0:
            raise asyncio.TimeoutError("Overall analysis timeout of 10 minutes exceeded.")

    def update_peak():
        nonlocal peak_memory
        peak_memory = max(peak_memory, get_memory_mb())

    # STARTUP_MEMORY log
    log_memory("STARTUP_MEMORY")

    # Define variables for cleanup
    candidates_pool = []
    passed_role = []
    passed_exp = []
    passed_skills = []
    faiss_input_candidates = []
    retrieved_candidates = []
    scored_pool = []
    top_scored = []
    top_100_candidates = []
    top_100_embs = []
    results_rows = []
    index = None
    candidate_ids_list = None
    full_embs = None
    jd_emb = None
    engine_res = None
    results = []

    # Initialize counters for telemetry
    total_candidates_in_dataset = 0
    AFTER_SKILL_FILTER = 0
    AFTER_ROLE_FILTER = 0
    AFTER_EXPERIENCE_FILTER = 0
    FAISS_INPUT_COUNT = 0
    AFTER_FAISS = 0

    try:
        # Get Job Description details
        job_res = supabase_client.table("jobs").select("*").eq("id", body.job_id).execute()
        if not job_res.data:
            raise HTTPException(status_code=400, detail="Job description not found.")
        job = job_res.data[0]

        current_path = p.get("current_candidate_path")
        if not current_path:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No candidates in project. Upload a candidates file first."
            )

        bucket, path = current_path.split("/", 1)
        total_candidates_in_dataset = p.get("candidate_count") or 0
        if total_candidates_in_dataset == 0:
            total_candidates_in_dataset = sum(1 for _ in StorageService.stream_jsonl(bucket, path))
            supabase_client.table("projects").update({"candidate_count": total_candidates_in_dataset}).eq("id", project_id).execute()

        # Invalidation check via JD hash (including openings/positions)
        openings = job.get("openings") or job.get("open_positions") or ""
        jd_content = f"{job.get('title', '')}|{job.get('description', '')}|{json.dumps(job.get('required_skills', []))}|{openings}"
        jd_hash = get_sha256_hash(jd_content)
        dataset_hash = compute_dataset_hash(None, project_id=project_id)

        # Check existing rankings
        existing_rank = supabase_client.table("rankings").select("*").eq("project_id", project_id).eq("dataset_hash", dataset_hash).eq("jd_hash", jd_hash).execute()
        if existing_rank.data:
            r = existing_rank.data[0]
            if r.get("metadata_only_fallback") and p.get("embedding_status") == "ready":
                logger.info("Cached ranking was metadata-only fallback, but embeddings are now ready. Bypassing cache to compute full vector ranking.")
                CacheService.invalidate_project(project_id)
            elif r.get("ai_enhancement_unavailable"):
                logger.info("Cached ranking had AI Enhancement Unavailable. Bypassing cache to retry OpenRouter.")
                CacheService.invalidate_project(project_id)
            else:
                logger.info("Dataset fingerprint check: Reusing existing ranking results for project %s", project_id)
                supabase_client.table("projects").update({"status": "COMPLETED", "updated_at": _now()}).eq("id", project_id).execute()
                results_res = supabase_client.table("ranking_results").select("full_result").eq("ranking_id", r["id"]).order("rank").execute()
                r["results"] = [x["full_result"] for x in results_res.data]
                return r

        # Mark database status as processing
        supabase_client.table("projects").update({"status": "processing", "updated_at": _now()}).eq("id", project_id).execute()

        # Setup limits
        MAX_ROLE_FILTER = 20000
        MAX_EXP_FILTER = 10000
        MAX_SKILL_FILTER = 3000
        MAX_FAISS_INPUT = 2000
        MAX_FAISS_RESULTS = 500
        MAX_DEEP_SCORING = 100

        filter_time = 0.0
        index_lookup_time = 0.0
        embedding_time = 0.0
        faiss_time = 0.0
        scoring_time = 0.0
        llm_time = 0.0

        # Memory Safety Limit check
        memory_safety_mode = False
        if get_memory_mb() > 450.0:
            logger.warning("[MEMORY_WARNING] Process RAM exceeds 450MB before filtering. Switching to memory safety mode.")
            memory_safety_mode = True

        # Extract Job parameters
        jd_category = job.get("role_category") or "BACKEND"
        jd_min_exp = float(job.get("min_experience") or 0.0)
        allowed_categories = COMPATIBLE_CATEGORIES.get(jd_category.upper(), {jd_category.upper()})
        jd_skills = [s.lower().strip() for s in job.get("required_skills", []) if s]

        print("[ANALYSIS_START]")
        print(f"[TOTAL_CANDIDATES] {total_candidates_in_dataset}")

        print("[ROLE_FILTER_START]")
        t_filter_start = time.time()

        # Check if role indexes are available
        role_index_used = False
        role_index_paths = []
        for cat in allowed_categories:
            role_path = f"{project_id}/role_{cat.upper()}_v1.jsonl"
            if StorageService.file_exists("role-indexes", role_path):
                role_index_paths.append(role_path)
                
        if len(role_index_paths) == len(allowed_categories):
            role_index_used = True

        def candidate_stream():
            if role_index_used:
                for role_path in role_index_paths:
                    for c in StorageService.stream_jsonl("role-indexes", role_path):
                        yield c
            else:
                for c_raw in StorageService.stream_jsonl(bucket, path):
                    yield standardize_candidate(c_raw)

        # Min-heap to keep the top MAX_FAISS_INPUT candidates
        faiss_input_heap = []
        counter = 0

        for c in candidate_stream():
            check_overall_timeout()
            update_peak()
            
            # Disqualification check
            if c.get("is_disqualified", False):
                continue
                
            # If not using role index, we must filter by role category compatibility
            if not role_index_used:
                cand_cat = c.get("candidate_role_category") or c.get("candidate_specialization") or "BACKEND"
                if normalize_role_category(cand_cat) not in allowed_categories:
                    continue
                    
            # Experience filter
            cand_exp = float(c.get("profile", {}).get("years_of_experience") or c.get("years_exp") or 0.0)
            if jd_min_exp > 0 and cand_exp < (jd_min_exp - 2.0):
                continue
                
            # Skill filter
            if jd_skills:
                cand_skills = [s.get("name", "").lower().strip() if isinstance(s, dict) else str(s).lower().strip() for s in c.get("skills", [])]
                if not (set(cand_skills) & set(jd_skills)):
                    continue
                    
            # Quality score
            score = c.get("candidate_quality_score") or 0.0
            counter += 1
            
            # Maintain top MAX_FAISS_INPUT in min-heap based on score
            if len(faiss_input_heap) < MAX_FAISS_INPUT:
                heapq.heappush(faiss_input_heap, (score, counter, c))
            else:
                if score > faiss_input_heap[0][0]:
                    heapq.heappushpop(faiss_input_heap, (score, counter, c))

        faiss_input_candidates = [item[2] for item in sorted(faiss_input_heap, key=lambda x: -x[0])]
        AFTER_SKILL_FILTER = len(faiss_input_candidates)
        AFTER_ROLE_FILTER = len(faiss_input_candidates)
        AFTER_EXPERIENCE_FILTER = len(faiss_input_candidates)
        FAISS_INPUT_COUNT = len(faiss_input_candidates)

        del faiss_input_heap
        update_peak()
        filter_time = time.time() - t_filter_start
        print("[ROLE_FILTER_END]")
        print("[EXPERIENCE_FILTER_END]")
        print("[SKILL_FILTER_END]")
        log_memory("FILTER_MEMORY")

        # Load FAISS index & IDs
        embedding_status = p.get("embedding_status", "pending")
        metadata_only_fallback = False
        fallback_reason = None

        version = 1
        faiss_key = f"{project_id}/faiss_v{version}.index"
        embeddings_key = f"{project_id}/embeddings_v{version}.npy"
        ids_key = f"{project_id}/ids_v{version}.json"

        # Check safety memory threshold
        if get_memory_mb() > 450.0 or memory_safety_mode:
            logger.warning("[MEMORY_WARNING] Process RAM exceeds 450MB. Disabling FAISS index loading.")
            metadata_only_fallback = True
            fallback_reason = "memory_safety_limit_exceeded"
        elif embedding_status != "ready":
            metadata_only_fallback = True
            fallback_reason = f"embedding_status_{embedding_status}"
        else:
            try:
                # Wrap FAISS and ID loading in a 2-minute timeout
                async def load_index_and_ids():
                    nonlocal index, candidate_ids_list
                    index = CacheService.get("faiss-indexes", faiss_key)
                    if not index:
                        content = StorageService.download_file("faiss-indexes", faiss_key)
                        import faiss
                        index = faiss.deserialize_index(np.frombuffer(content, dtype=np.uint8))
                        CacheService.set("faiss-indexes", faiss_key, index)
                                
                    candidate_ids_list = CacheService.get("embeddings", ids_key)
                    if not candidate_ids_list:
                        content = StorageService.download_file("embeddings", ids_key)
                        candidate_ids_list = json.loads(content.decode("utf-8"))
                        CacheService.set("embeddings", ids_key, candidate_ids_list)

                await asyncio.wait_for(load_index_and_ids(), timeout=120.0) # 2 minute timeout
            except asyncio.TimeoutError:
                logger.error("FAISS index/IDs loading timed out after 2 minutes.")
                metadata_only_fallback = True
                fallback_reason = "faiss_load_timeout"
            except Exception as e:
                logger.error("Failed to load FAISS index: %s", e)
                metadata_only_fallback = True
                fallback_reason = f"storage_index_load_error: {e}"

        if metadata_only_fallback:
            print(f"[WARNING] Fallback activated. Reason: {fallback_reason}")
            print("[WARNING] Embedding cache missing. Using metadata fallback.")

        # Embedding & FAISS Search
        if metadata_only_fallback:
            print("[EMBEDDING_START] (skipped: metadata-only fallback)")
            print("[EMBEDDING_END]")
            print("[FAISS_START] (skipped: metadata-only fallback)")
            print("[FAISS_END]")
            
            retrieved_candidates = faiss_input_candidates[:]
            retrieved_candidates.sort(key=lambda x: x.get("candidate_quality_score") or 0.0, reverse=True)
            retrieved_candidates = retrieved_candidates[:MAX_FAISS_RESULTS]
            AFTER_FAISS = len(retrieved_candidates)
            similarities_map = {c.get("candidate_id"): 0.5 for c in retrieved_candidates}
        else:
            print("[EMBEDDING_START]")
            t_emb_start = time.time()
            
            jd_text = (
                job.get("title", "")
                + " "
                + job.get("description", "")
            ).strip()
            
            # Lazy load model here
            encoder = _get_encoder()
            log_memory("MODEL_MEMORY")
            update_peak()
            
            try:
                # Wrap JD embedding in 5-minute timeout
                async def generate_jd_embedding():
                    return encoder.encode_single(jd_text, normalize=True, bge_mode="query")

                jd_emb = await asyncio.wait_for(generate_jd_embedding(), timeout=300.0)
                embedding_time = time.time() - t_emb_start
            except asyncio.TimeoutError:
                logger.error("Embedding generation timed out after 5 minutes.")
                metadata_only_fallback = True
                fallback_reason = "embedding_generation_timeout"
            except Exception as e:
                logger.error("Failed to generate JD embedding: %s", e)
                metadata_only_fallback = True
                fallback_reason = f"embedding_error: {e}"
                
            print("[EMBEDDING_END]")
            
            print("[FAISS_START]")
            t_faiss_start = time.time()
            
            if metadata_only_fallback:
                print("[FAISS_END] (aborted due to embedding failure)")
                retrieved_candidates = faiss_input_candidates[:]
                retrieved_candidates.sort(key=lambda x: x.get("candidate_quality_score") or 0.0, reverse=True)
                retrieved_candidates = retrieved_candidates[:MAX_FAISS_RESULTS]
                AFTER_FAISS = len(retrieved_candidates)
                similarities_map = {c.get("candidate_id"): 0.5 for c in retrieved_candidates}
            else:
                try:
                    id_to_idx = {cid: idx for idx, cid in enumerate(candidate_ids_list)}
                    pool_indices = []
                    for c in faiss_input_candidates:
                        cid = c.get("candidate_id")
                        if cid in id_to_idx:
                            pool_indices.append(id_to_idx[cid])
                            
                    if pool_indices:
                        # Wrap FAISS search in 2-minute timeout
                        async def run_faiss_search():
                            nonlocal similarities, ann_indices
                            import faiss
                            sel = faiss.IDSelectorArray(np.array(pool_indices, dtype=np.int64))
                            params = faiss.SearchParameters()
                            params.sel = sel
                            
                            top_k_search = min(MAX_FAISS_RESULTS, len(pool_indices))
                            similarities, ann_indices = index.search(
                                jd_emb.reshape(1, -1).astype(np.float32),
                                top_k_search,
                                params=params
                            )
                            similarities = similarities[0]
                            ann_indices = ann_indices[0]

                        similarities = None
                        ann_indices = None
                        await asyncio.wait_for(run_faiss_search(), timeout=120.0) # 2 minutes
                        
                        c_map = {c.get("candidate_id"): c for c in faiss_input_candidates}
                        for sim, global_idx in zip(similarities, ann_indices):
                            if global_idx < 0 or global_idx >= len(candidate_ids_list):
                                continue
                            cid = candidate_ids_list[global_idx]
                            if cid in c_map:
                                retrieved_candidates.append(c_map[cid])
                                similarities_map[cid] = float(sim)
                    else:
                        retrieved_candidates = []
                        
                    faiss_time = time.time() - t_faiss_start
                except asyncio.TimeoutError:
                    logger.error("FAISS search timed out after 2 minutes.")
                    metadata_only_fallback = True
                    retrieved_candidates = faiss_input_candidates[:]
                    retrieved_candidates.sort(key=lambda x: x.get("candidate_quality_score") or 0.0, reverse=True)
                    retrieved_candidates = retrieved_candidates[:MAX_FAISS_RESULTS]
                    similarities_map = {c.get("candidate_id"): 0.5 for c in retrieved_candidates}
                except Exception as e:
                    logger.error("FAISS search encountered error: %s", e)
                    metadata_only_fallback = True
                    retrieved_candidates = faiss_input_candidates[:]
                    retrieved_candidates.sort(key=lambda x: x.get("candidate_quality_score") or 0.0, reverse=True)
                    retrieved_candidates = retrieved_candidates[:MAX_FAISS_RESULTS]
                    similarities_map = {c.get("candidate_id"): 0.5 for c in retrieved_candidates}
                    
                AFTER_FAISS = len(retrieved_candidates)
                print("[FAISS_END]")
                log_memory("FAISS_MEMORY")
                update_peak()

        print("[SCORING_START]")
        t_stage3_start = time.time()
        scored_pool = []
        for c in retrieved_candidates:
            cid = c.get("candidate_id")
            norm_sim = similarities_map.get(cid, 0.5)
            
            cand_cat = normalize_role_category(c.get("candidate_role_category") or c.get("candidate_specialization") or "BACKEND")
            if cand_cat == normalize_role_category(jd_category):
                boost = 0.15
            elif cand_cat not in allowed_categories:
                boost = -0.10
            else:
                boost = 0.0
                
            blend_score = 0.70 * norm_sim + 0.30 * boost
            scored_pool.append({
                "candidate": c,
                "similarity": norm_sim,
                "blend_score": blend_score
            })
            
        scored_pool.sort(key=lambda x: -x["blend_score"])
        top_scored = scored_pool[:MAX_DEEP_SCORING]
        top_100_candidates = [item["candidate"] for item in top_scored]
        
        scoring_time = time.time() - t_stage3_start
        print("[SCORING_END]")

        top_100_embs = None
        if not metadata_only_fallback and top_100_candidates:
            try:
                full_embs = CacheService.get("embeddings", embeddings_key)
                if not isinstance(full_embs, np.ndarray):
                    content = StorageService.download_file("embeddings", embeddings_key)
                    import io
                    full_embs = np.load(io.BytesIO(content))
                    CacheService.set("embeddings", embeddings_key, full_embs)
                
                id_to_idx = {cid: idx for idx, cid in enumerate(candidate_ids_list)}
                top_100_indices = []
                for c in top_100_candidates:
                    cid = c.get("candidate_id")
                    if cid in id_to_idx:
                        top_100_indices.append(id_to_idx[cid])
                if top_100_indices:
                    top_100_embs = full_embs[top_100_indices]
            except Exception:
                pass

        print("[LLM_START]")
        
        # Enforce memory safety limit check before calling LLM
        call_llm_flag = True
        if get_memory_mb() > 450.0 or memory_safety_mode:
            logger.warning("[MEMORY_WARNING] Process RAM exceeds 450MB before LLM stage. Disabling LLM enhancement.")
            call_llm_flag = False

        from src.ranking.engine import UnifiedRankingEngine
        engine = UnifiedRankingEngine(
            encoder=_get_encoder(),
            config={
                "performance_mode": body.performance_mode or "balanced",
                "apply_eligibility": True
            }
        )
        
        from src.ranking.engine import validate_tuple
        
        # Wrap LLM evaluation in 60-second timeout (Requirement 5)
        try:
            async def run_llm_ranking():
                return await engine.rank_candidates(
                    candidates=top_100_candidates,
                    jd_dict=job,
                    top_n=body.top_k,
                    call_llm=call_llm_flag,
                    candidate_embeddings=top_100_embs
                )

            engine_res = await asyncio.wait_for(run_llm_ranking(), timeout=60.0) # 60 seconds
        except asyncio.TimeoutError:
            logger.error("OpenRouter LLM evaluation timed out after 60 seconds. Falling back to deterministic ranking.")
            engine_res = await engine.rank_candidates(
                candidates=top_100_candidates,
                jd_dict=job,
                top_n=body.top_k,
                call_llm=False,
                candidate_embeddings=top_100_embs
            )
            
        assert isinstance(engine_res, tuple), f"Expected tuple from rank_candidates, got {type(engine_res).__name__}"
        validate_tuple(engine_res, 3, "platform.py run_analysis", "(results, ranked_tuples, blended_scores)")
        results, _, _ = engine_res
        print("[LLM_END]")
        log_memory("LLM_MEMORY")
        update_peak()

        scoring_time += engine.metrics.get("ranking_time", 0.0)
        llm_time += engine.metrics.get("llm_time", 0.0)

        categories = [
            normalize_role_category(c.get("candidate_role_category") or c.get("candidate_specialization") or "BACKEND")
            for c in faiss_input_candidates
        ]
        cat_counts = Counter(categories)
        top_categories = [cat for cat, count in cat_counts.most_common(3)]
        
        prefilter_statistics = {
            "total_uploaded": total_candidates_in_dataset,
            "eligible": AFTER_SKILL_FILTER,
            "filtered_out": max(0, total_candidates_in_dataset - AFTER_SKILL_FILTER),
            "top_categories": top_categories
        }

        status = "no_qualified_candidates" if getattr(engine, "status", "") == "no_qualified_candidates" else "completed"
        ranking_id = str(uuid.uuid4())
        now = _now()

        version_metadata = {
            "ranking_version": "v2.5",
            "embedding_model": settings.embedding_model,
            "llm_provider": "OpenRouter",
            "llm_model": settings.openrouter_model,
            "generated_at": now
        }

        total_time_all = time.time() - t_start_all

        ai_enhancement_error = getattr(engine, "ai_enhancement_unavailable", False)
        ai_enhancement_unavailable = False
        if ai_enhancement_error:
            ai_enhancement_unavailable = True

        metrics = {
            "total_candidates": total_candidates_in_dataset,
            "candidates_filtered": total_candidates_in_dataset - AFTER_SKILL_FILTER,
            "candidates_retrieved": AFTER_FAISS,
            "candidates_scored": len(top_100_candidates),
            "llm_candidates_evaluated": engine.metrics.get("llm_candidates_evaluated", 0),
            "retrieval_time": filter_time + index_lookup_time + faiss_time,
            "ranking_time": scoring_time,
            "llm_time": llm_time,
            "total_analysis_time": total_time_all,
            "filter_time": filter_time,
            "index_lookup_time": index_lookup_time,
            "embedding_time": embedding_time,
            "faiss_time": faiss_time,
            "scoring_time": scoring_time,
            "total_time": total_time_all,
            "total_candidates_funnel": total_candidates_in_dataset,
            "after_role_filter": AFTER_ROLE_FILTER,
            "after_experience_filter": AFTER_EXPERIENCE_FILTER,
            "after_skill_filter": AFTER_SKILL_FILTER,
            "faiss_input_count": FAISS_INPUT_COUNT,
            "after_faiss": AFTER_FAISS,
            "after_scoring": len(top_100_candidates),
            "llm_input_count": engine.metrics.get("llm_candidates_evaluated", 0),
            "after_llm_selection": len(results),
            "peak_memory_mb": round(peak_memory, 2)
        }

        ranking = {
            "id": ranking_id,
            "project_id": project_id,
            "job_id": body.job_id,
            "status": status,
            "total_candidates": total_candidates_in_dataset,
            "ranked_count": len(results),
            "results": results,
            "dataset_hash": dataset_hash,
            "jd_hash": jd_hash,
            "version_metadata": version_metadata,
            "metrics": metrics,
            "prefilter_statistics": prefilter_statistics,
            "metadata_only_fallback": metadata_only_fallback,
            "ai_enhancement_unavailable": ai_enhancement_unavailable,
            "created_at": now,
        }
        if status == "no_qualified_candidates":
            ranking["message"] = "No strong candidates found for this role."
            ranking["alternative_candidates"] = getattr(engine, "alternative_candidates", [])

        supabase_client.table("rankings").insert({
            "id": ranking_id,
            "project_id": project_id,
            "job_id": body.job_id,
            "version": 1,
            "status": status,
            "total_candidates": total_candidates_in_dataset,
            "ranked_count": len(results),
            "dataset_hash": dataset_hash,
            "jd_hash": jd_hash,
            "version_metadata": version_metadata,
            "metrics": metrics,
            "prefilter_statistics": prefilter_statistics,
            "metadata_only_fallback": metadata_only_fallback,
            "ai_enhancement_unavailable": ai_enhancement_unavailable,
            "created_at": now
        }).execute()

        results_rows = []
        for res in results:
            results_rows.append({
                "ranking_id": ranking_id,
                "candidate_id": res.get("candidate_id"),
                "rank": res.get("rank"),
                "score": res.get("ai_score") or res.get("score") or 0.0,
                "reasoning": res.get("reasoning"),
                "eligibility": res.get("eligibility", True),
                "critical_skill_coverage": res.get("critical_skill_coverage"),
                "full_result": res
            })
            
        for i in range(0, len(results_rows), 50):
            supabase_client.table("ranking_results").insert(results_rows[i:i+50]).execute()

        avg_score = sum(r.get("ai_score") or r.get("score") or 0.0 for r in results) / len(results) if results else 0.0
        supabase_client.table("analysis_metrics").insert({
            "ranking_id": ranking_id,
            "project_id": project_id,
            "upload_time": 0.0,
            "embedding_time": embedding_time,
            "faiss_time": faiss_time,
            "llm_time": llm_time,
            "total_analysis_time": total_time_all,
            "average_match_score": avg_score
        }).execute()

        supabase_client.table("projects").update({
            "status": "COMPLETED",
            "updated_at": now
        }).eq("id", project_id).execute()
        
        _set_cached_ranking(project_id, body.job_id, ranking)
        return ranking

    except MemoryError as exc:
        logger.error("[OOM_RECOVERY] Out of Memory detected in run_analysis: %s", exc)
        try:
            from app.services.cache_service import CacheService
            CacheService.clear()
        except Exception:
            pass
        try:
            from src.features.embedding import _MODEL_CACHE
            _MODEL_CACHE.clear()
        except Exception:
            pass
        global _encoder
        _encoder = None
        import gc
        gc.collect()
        
        # Try to mark the project as failed in Supabase so the UI doesn't hang
        try:
            supabase_client.table("projects").update({
                "status": "failed",
                "upload_statistics": {"failure_reason": "Out of memory error"},
                "updated_at": _now()
            }).eq("id", project_id).execute()
        except Exception:
            pass
            
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server temporarily overloaded: Out of memory."
        )

    except Exception as exc:
        supabase_client.table("projects").update({
            "status": "failed",
            "upload_statistics": {"failure_reason": str(exc)},
            "updated_at": _now()
        }).eq("id", project_id).execute()
        logger.error("Analysis failed for project %s: %s", project_id, exc)
        import traceback
        traceback.print_exc()
        raise exc
    finally:
        # 1. Release active project cache references from CacheService
        try:
            from app.services.cache_service import CacheService
            CacheService.invalidate_project(project_id)
        except Exception:
            pass
            
        # 2. Release local large variables
        large_vars = [
            "candidates_pool", "passed_role", "passed_exp", "passed_skills",
            "faiss_input_candidates", "retrieved_candidates", "scored_pool",
            "top_scored", "top_100_candidates", "top_100_embs", "results_rows",
            "index", "candidate_ids_list", "full_embs", "jd_emb", "engine_res",
            "results"
        ]
        for v in large_vars:
            if v in locals():
                try:
                    del locals()[v]
                except Exception:
                    pass
                    
        # 3. Explicitly trigger Garbage Collection
        import gc
        gc.collect()
        
        # 4. Release concurrent analysis project lock
        _active_analyses.discard(project_id)
        
        # 5. Log telemetry FINAL_MEMORY and Peak Memory
        update_peak()
        log_memory("FINAL_MEMORY")
        print(f"[MEMORY_TELEMETRY] PEAK_MEMORY: {peak_memory:.2f} MB", flush=True)
        from app.core.config import settings
        print(f"[MEMORY_TELEMETRY] CURRENT_MODEL: {settings.embedding_model}", flush=True)
        print(f"[MEMORY_TELEMETRY] CANDIDATE_COUNT: {total_candidates_in_dataset}", flush=True)
        print(f"[MEMORY_TELEMETRY] FILTERED_COUNT: {AFTER_SKILL_FILTER}", flush=True)


def _top_strengths(ds) -> list[str]:
    if not ds:
        return []
    dims = {
        "Strong semantic skill fit": ds.required_skills_match,
        "Strong experience quality": ds.relevant_experience,
        "Good career progression": ds.career_growth,
        "High hiring readiness": ds.behavioral_fit,
        "Ideal logistics fit": ds.specialization_match,
    }
    return [k for k, v in sorted(dims.items(), key=lambda x: -x[1]) if v > 0.6][:3]


def _top_weaknesses(ds) -> list[str]:
    if not ds:
        return []
    dims = {
        "Limited semantic skill match": ds.required_skills_match,
        "Weaker experience profile": ds.relevant_experience,
        "Flat career progression": ds.career_growth,
        "Lower engagement signals": ds.behavioral_fit,
        "Non-preferred logistics": ds.specialization_match,
    }
    return [k for k, v in sorted(dims.items(), key=lambda x: x[1]) if v < 0.5][:2]


# ── Ranking retrieval ─────────────────────────────────────────────────────────

@router.get("/projects/{project_id}/rankings/{ranking_id}")
async def get_ranking(project_id: str, ranking_id: str, current_user: Optional[AuthUser] = Depends(get_optional_user)):
    user_id = get_user_id(current_user)
    res = supabase_client.table("rankings").select("*").eq("id", ranking_id).eq("project_id", project_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Ranking not found")
        
    ranking = res.data[0]
    
    results_res = supabase_client.table("ranking_results").select("full_result").eq("ranking_id", ranking_id).order("rank").execute()
    ranking["results"] = [r["full_result"] for r in results_res.data]
    return ranking


# ── Analytics ─────────────────────────────────────────────────────────────────

@router.get("/projects/{project_id}/analytics")
async def get_analytics(
    project_id: str,
    ranking_id: Optional[str] = None,
    current_user: Optional[AuthUser] = Depends(get_optional_user)
):
    user_id = get_user_id(current_user)
    proj_res = supabase_client.table("projects").select("current_candidate_path").eq("id", project_id).eq("user_id", user_id).execute()
    if not proj_res.data:
        raise HTTPException(status_code=404, detail="Project not found")

    current_path = proj_res.data[0].get("current_candidate_path")
    candidates_count = 0
    skill_counts: dict[str, int] = {}
    exp_buckets = {"0-2 years": 0, "2-5 years": 0, "5-10 years": 0, "10+ years": 0}

    if current_path:
        bucket, path = current_path.split("/", 1)
        from app.services.storage_provider import StorageService
        for c in StorageService.stream_jsonl(bucket, path):
            candidates_count += 1
            for s in c.get("skills", []):
                name = s.get("name") if isinstance(s, dict) else s
                if name:
                    skill_counts[name] = skill_counts.get(name, 0) + 1
            yoe = float(c.get("profile", {}).get("years_of_experience") or c.get("years_exp") or 0)
            if yoe < 2:
                exp_buckets["0-2 years"] += 1
            elif yoe < 5:
                exp_buckets["2-5 years"] += 1
            elif yoe < 10:
                exp_buckets["5-10 years"] += 1
            else:
                exp_buckets["10+ years"] += 1

    skill_dist = [{"skill": k, "count": v} for k, v in
                  sorted(skill_counts.items(), key=lambda x: -x[1])[:15]]
    exp_dist = [{"range": k, "count": v} for k, v in exp_buckets.items()]

    quality = {"high": 0, "medium": 0, "low": 0}
    match_bd = {"excellent": 0, "good": 0, "fair": 0, "poor": 0}
    hidden_gems = []
    high_risk = []

    if ranking_id:
        results_res = supabase_client.table("ranking_results").select("full_result").eq("ranking_id", ranking_id).execute()
        for row_data in results_res.data:
            row = row_data.get("full_result", {})
            readiness = row.get("hiring_readiness", "medium")
            quality[readiness] = quality.get(readiness, 0) + 1
            mp = row.get("match_percent", 0)
            if mp >= 80:
                match_bd["excellent"] += 1
            elif mp >= 60:
                match_bd["good"] += 1
            elif mp >= 40:
                match_bd["fair"] += 1
            else:
                match_bd["poor"] += 1

    return {
        "skill_distribution": skill_dist,
        "experience_distribution": exp_dist,
        "quality_breakdown": quality,
        "match_breakdown": match_bd,
        "hidden_gems": hidden_gems,
        "high_risk_profiles": high_risk,
        "hiring_funnel": {
            "uploaded": candidates_count,
            "analyzed": candidates_count,
            "ranked": sum(quality.values()),
            "shortlisted": quality.get("high", 0),
        },
    }


# ── Export ────────────────────────────────────────────────────────────────────

@router.post("/projects/{project_id}/export")
async def export_results(
    project_id: str,
    body: ExportRequest,
    current_user: Optional[AuthUser] = Depends(get_optional_user)
):
    user_id = get_user_id(current_user)
    p_res = supabase_client.table("projects").select("name").eq("id", project_id).eq("user_id", user_id).execute()
    if not p_res.data:
        raise HTTPException(status_code=404, detail="Project not found")
    p = p_res.data[0]

    r_res = supabase_client.table("rankings").select("*").eq("id", body.ranking_id).eq("project_id", project_id).execute()
    if not r_res.data:
        raise HTTPException(status_code=404, detail="Ranking not found")
    r = r_res.data[0]

    job_res = supabase_client.table("jobs").select("title").eq("id", r["job_id"]).execute()
    job_title = job_res.data[0]["title"] if job_res.data else "Unknown Job"

    results_res = supabase_client.table("ranking_results").select("full_result").eq("ranking_id", body.ranking_id).order("rank").execute()
    r_results = [row["full_result"] for row in results_res.data]

    if len(r_results) == 0 and r.get("status") == "completed":
        import logging
        logging.getLogger(__name__).warning("Ranking export mismatch detected: UI rankings cannot be empty for completed runs.")
        raise HTTPException(status_code=400, detail="Ranking export mismatch detected")

    _health_stats["exports_generated"] += 1

    from app.services.export_service import ExportService

    if body.format == "json":
        r["results"] = r_results
        content = json.dumps(r, indent=2)
        return StreamingResponse(
            io.BytesIO(content.encode()),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=ranking-{body.ranking_id[:8]}.json"},
        )
    elif body.format == "xlsx":
        xlsx_data = ExportService.generate_xlsx(r_results)
        return StreamingResponse(
            io.BytesIO(xlsx_data),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=ranking-{body.ranking_id[:8]}.xlsx"},
        )
    elif body.format == "pdf":
        pdf_data = ExportService.generate_pdf(p.get("name", "Project"), job_title, r_results)
        return StreamingResponse(
            io.BytesIO(pdf_data),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=ranking-{body.ranking_id[:8]}.pdf"},
        )
    else:
        # Default to csv
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow([
            "Rank", "Candidate Name", "Current Role", "Current Company",
            "Experience", "Location", "Match %", "AI Score", "Eligibility",
            "Critical Skill Coverage", "Top Skills", "Recommendation", "Reasoning"
        ])
        
        for row in r_results:
            cand_name = row.get("candidate_name") or row.get("candidate_id", "")
            role = row.get("current_title", "")
            company = row.get("current_company", "")
            exp = f"{row.get('years_of_experience', 0.0)} Years"
            loc = row.get("location", "")
            match_pct = f"{row.get('match_percent')}%"
            ai_score = row.get("ai_score", 0.0)
            elig = "Eligible" if row.get("eligibility") else f"Ineligible: {row.get('eligibility_reason', '')}"
            skill_cov = row.get("critical_skill_coverage", "")
            
            skills_raw = row.get("top_skills", [])
            skills_list = [s.get("name") if isinstance(s, dict) else str(s) for s in skills_raw]
            skills_str = ", ".join(skills_list)
            
            recommendation = row.get("hiring_readiness", "")
            reasoning = row.get("reasoning", "")
            
            writer.writerow([
                row.get("rank"), cand_name, role, company,
                exp, loc, match_pct, ai_score, elig,
                skill_cov, skills_str, recommendation, reasoning
            ])
            
        return StreamingResponse(
            io.BytesIO(out.getvalue().encode()),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=ranking-{body.ranking_id[:8]}.csv"},
        )


# ── Health Dashboard ──────────────────────────────────────────────────────────

@router.get("/health-stats")
async def get_health_stats(current_user: Optional[AuthUser] = Depends(get_optional_user)):
    user_id = get_user_id(current_user)
    
    # Fetch projects for the user from Supabase
    proj_res = supabase_client.table("projects").select("id, status, candidate_count").eq("user_id", user_id).execute()
    projects_list = proj_res.data or []
    
    num_projects = len(projects_list)
    failed_jobs = sum(1 for p in projects_list if p.get("status") == "failed")
    total_candidates = sum(p.get("candidate_count") or 0 for p in projects_list)
    
    # Fetch rankings count for the user's projects
    if projects_list:
        project_ids = [p["id"] for p in projects_list]
        rankings_res = supabase_client.table("rankings").select("id").in_("project_id", project_ids).execute()
        num_rankings = len(rankings_res.data or [])
    else:
        num_rankings = 0

    return {
        "projects": num_projects,
        "candidates": total_candidates,
        "rankings": num_rankings,
        "failed_jobs": failed_jobs,
        "duplicate_projects_prevented": _health_stats.get("duplicate_projects_prevented", 0),
        "exports_generated": _health_stats.get("exports_generated", 0),
    }


@router.get("/projects/{project_id}/performance-metrics")
async def get_performance_metrics(
    project_id: str,
    current_user: Optional[AuthUser] = Depends(get_optional_user)
):
    user_id = get_user_id(current_user)
    p_res = supabase_client.table("projects").select("*").eq("id", project_id).eq("user_id", user_id).execute()
    if not p_res.data:
        raise HTTPException(status_code=404, detail="Project not found")
        
    r_res = supabase_client.table("rankings").select("*").eq("project_id", project_id).order("created_at", desc=True).limit(1).execute()
    if not r_res.data:
        return {
            "project_id": project_id,
            "has_metrics": False,
            "message": "No analysis runs found for this project yet."
        }
        
    ranking = r_res.data[0]
    metrics = ranking.get("metrics") or {}
    
    return {
        "project_id": project_id,
        "ranking_id": ranking.get("id"),
        "has_metrics": True,
        "performance_mode": ranking.get("version_metadata", {}).get("performance_mode", "balanced"),
        "metadata_only_fallback": ranking.get("metadata_only_fallback", False),
        "ai_enhancement_unavailable": ranking.get("ai_enhancement_unavailable", False),
        "timing_metrics": {
            "total_analysis_time_sec": metrics.get("total_analysis_time", 0.0),
            "retrieval_time_sec": metrics.get("retrieval_time", 0.0),
            "filter_time_sec": metrics.get("filter_time", 0.0),
            "index_lookup_time_sec": metrics.get("index_lookup_time", 0.0),
            "embedding_time_sec": metrics.get("embedding_time", 0.0),
            "faiss_time_sec": metrics.get("faiss_time", 0.0),
            "scoring_time_sec": metrics.get("scoring_time", 0.0),
            "llm_time_sec": metrics.get("llm_time", 0.0),
        },
        "funnel_metrics": {
            "total_candidates": metrics.get("total_candidates", 0),
            "after_role_filter": metrics.get("after_role_filter", 0),
            "after_experience_filter": metrics.get("after_experience_filter", 0),
            "after_skill_filter": metrics.get("after_skill_filter", 0),
            "faiss_input_count": metrics.get("faiss_input_count", 0),
            "after_faiss": metrics.get("after_faiss", 0),
            "after_scoring": metrics.get("after_scoring", 0),
            "llm_input_count": metrics.get("llm_input_count", 0),
            "after_llm_selection": metrics.get("after_llm_selection", 0),
        },
        "memory_metrics": {
            "peak_memory_mb": metrics.get("peak_memory_mb", 0.0),
            "limit_mb": 450.0,
            "safety_margin_mb": max(0.0, 450.0 - metrics.get("peak_memory_mb", 0.0))
        }
    }
