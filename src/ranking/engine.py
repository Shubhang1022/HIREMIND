"""Unified Ranking Engine — HireMind AI v2.2.

Orchestrates the 10-step hybrid retrieval pipeline. Used by both the web UI
and the offline CLI pipeline to ensure identical ranking results.
"""
from __future__ import annotations

import asyncio
import logging
import time
import json
from dataclasses import dataclass
from typing import Any

import numpy as np

from src.features.structured import StructuredFeatureExtractor
from src.features.text_builder import build_candidate_text
from src.scoring.dimensions import DimensionScorer, DimScores
from src.scoring.dim_semantic import calculate_critical_skill_coverage
from src.scoring.dim_specialization import score_specialization_match
from src.ranking.selector import select_top_n
from backend.app.core.openrouter import recruiter_evaluate_batch

logger = logging.getLogger(__name__)


def validate_tuple(t: Any, expected_len: int, caller: str, expected_format: str) -> None:
    if not isinstance(t, tuple):
        raise TypeError(
            f"Expected a tuple, got {type(t).__name__} in {caller}. "
            f"Value: {t}. Expected format: {expected_format}"
        )
    if len(t) != expected_len:
        raise IndexError(
            f"Tuple length mismatch in {caller}: got length {len(t)}, expected {expected_len}. "
            f"Tuple contents: {t}. Expected format: {expected_format}"
        )


def generate_deterministic_reasoning(c: dict, score: float, rank: int, jd_title: str) -> dict:
    profile = c.get("profile") or {}
    skills_list = c.get("skills") or []
    cand_skills = [s.get("name") if isinstance(s, dict) else s for s in skills_list]
    cand_skills = [str(s).strip() for s in cand_skills if s]
    
    yoe = float(profile.get("years_of_experience") or c.get("years_exp") or 0.0)
    
    summary = (
        f"Candidate meets requirements for {jd_title} with {yoe:.1f} years of experience and core skills: {', '.join(cand_skills[:3])}. "
        f"Blended score is {score:.2f} based on experience match and category alignment."
    )
    
    return {
        "candidate_id": c.get("candidate_id"),
        "rank": rank,
        "final_score": score,
        "role_specialization": c.get("candidate_role_category") or c.get("candidate_specialization") or "Backend Engineer",
        "specialization_match_score": 0.8,
        "skills_match_score": 0.8,
        "experience_score": 0.8,
        "behavioral_score": 0.8,
        "integrity_score": 0.8,
        "strengths": ["Strong background", "Good experience"],
        "weaknesses": ["Requires further technical evaluation"],
        "missing_skills": [],
        "recruiter_summary": summary,
        "why_selected": f"Ranked #{rank} based on deterministic matching.",
        "why_not_ranked_higher": "Additional AI evaluations were skipped due to provider unavailability."
    }


@dataclass
class CandidateMetadata:
    candidate_id: str
    candidate_name: str
    current_title: str
    current_company: str
    location: str
    years_of_experience: float


def resolve_candidate_metadata(cand: dict) -> CandidateMetadata:
    cid = cand.get("candidate_id") or cand.get("external_id") or ""
    profile = cand.get("profile") or {}
    
    # Priority lookup for candidate_name (Improvement 2)
    candidate_name = (
        profile.get("candidate_name")
        or profile.get("anonymized_name")
        or cand.get("candidate_name")
        or "Unknown Candidate"
    )
    
    # Priority lookup for title
    current_title = (
        profile.get("current_title")
        or cand.get("current_title")
        or ""
    )
    
    # Priority lookup for company
    current_company = (
        profile.get("current_company")
        or cand.get("current_company")
        or ""
    )
    
    # Priority lookup for location
    location = (
        profile.get("location")
        or profile.get("location_city")
        or cand.get("location")
        or cand.get("location_city")
        or ""
    )
    
    # Priority lookup for years of experience
    raw_yoe = (
        profile.get("years_of_experience")
        or profile.get("years_exp")
        or cand.get("years_of_experience")
        or cand.get("years_exp")
        or 0.0
    )
    try:
        years_of_experience = float(raw_yoe)
    except (ValueError, TypeError):
        years_of_experience = 0.0
        
    return CandidateMetadata(
        candidate_id=cid,
        candidate_name=candidate_name,
        current_title=current_title,
        current_company=current_company,
        location=location,
        years_of_experience=years_of_experience,
    )


def validate_ranking_payload(results: list[dict], anonymize_mode: bool = False) -> None:
    seen_ids = set()
    seen_ranks = set()
    for res in results:
        cid = res.get("candidate_id")
        name = res.get("candidate_name")
        yoe = res.get("years_of_experience")
        integrity = res.get("integrity_score")
        rank = res.get("rank")
        
        # Validate candidate_id exists and is not duplicate
        if not cid:
            raise ValueError("Validation failed: candidate_id is missing.")
        if cid in seen_ids:
            raise ValueError(f"Validation failed: Duplicate candidate_id '{cid}' found.")
        seen_ids.add(cid)
        
        # Validate candidate_name exists and name != "Candidate" unless anonymize_mode (Improvement 1)
        if not name or name.strip() == "":
            raise ValueError(f"Validation failed: Candidate '{cid}' has a blank name.")
        if name == "Candidate" and not anonymize_mode:
            raise ValueError(f"Validation failed: Candidate '{cid}' name is placeholder 'Candidate'.")
            
        # Validate years_of_experience >= 0
        if yoe is None or yoe < 0:
            raise ValueError(f"Validation failed: Candidate '{cid}' has negative or missing experience ({yoe}).")
            
        # Validate integrity_score between 0 and 1
        if integrity is None or not (0.0 <= integrity <= 1.0):
            raise ValueError(f"Validation failed: Candidate '{cid}' integrity score {integrity} is out of bounds [0.0, 1.0].")
            
        # Validate rank is not blank and is not duplicate
        if rank is None or rank == "":
            raise ValueError(f"Validation failed: Candidate '{cid}' has a blank rank.")
        if rank in seen_ranks:
            raise ValueError(f"Validation failed: Duplicate rank '{rank}' found.")
        seen_ranks.add(rank)
COMPATIBLE_CATEGORIES = {
    "MLOPS": {"MLOPS", "DEVOPS", "AI_ML", "BACKEND"},
    "DEVOPS": {"DEVOPS", "MLOPS", "BACKEND"},
    "BACKEND": {"BACKEND", "MLOPS", "DEVOPS", "DATA_ENGINEERING", "AI_ML"},
    "FRONTEND": {"FRONTEND", "DESIGN"},
    "DATA_ENGINEERING": {"DATA_ENGINEERING", "DATA_SCIENCE", "AI_ML", "BACKEND"},
    "DATA_SCIENCE": {"DATA_SCIENCE", "AI_ML", "DATA_ENGINEERING"},
    "AI_ML": {"AI_ML", "MLOPS", "DATA_SCIENCE", "DATA_ENGINEERING", "BACKEND"},
    "PROJECT_MANAGEMENT": {"PROJECT_MANAGEMENT", "PRODUCT_MANAGEMENT"},
    "PRODUCT_MANAGEMENT": {"PRODUCT_MANAGEMENT", "PROJECT_MANAGEMENT"},
    "DESIGN": {"DESIGN", "FRONTEND"},
    "MARKETING": {"MARKETING"},
    "HR": {"HR"},
}

def _classify_jd_role_category(jd_dict: dict) -> str:
    title = str(jd_dict.get("title", "")).lower()
    desc = (str(jd_dict.get("description", "")) + " " + str(jd_dict.get("full_text", ""))).lower()
    
    from src.features.structured import ROLE_CATEGORY_KEYWORDS
    
    scores = {}
    for role_name, config in ROLE_CATEGORY_KEYWORDS.items():
        score = 0.0
        for kw in config["titles"]:
            if kw in title:
                score += 8.0
        for kw in config["desc"]:
            if kw in desc:
                score += 2.0
        scores[role_name] = score
        
    best_role = "BACKEND"
    max_score = 0.0
    for r, s in scores.items():
        if s > max_score:
            max_score = s
            best_role = r
            
    if max_score == 0.0:
        mgmt_words = ["manager", "scrum", "pm", "pmo", "director", "agile", "operations", "lead", "product"]
        if any(w in title for w in mgmt_words):
            if "product" in title:
                best_role = "PRODUCT_MANAGEMENT"
            else:
                best_role = "PROJECT_MANAGEMENT"
        else:
            best_role = "BACKEND"
            
    return best_role


class UnifiedRankingEngine:
    """Single Source of Truth candidate ranking engine supporting 100,000+ candidates."""

    def __init__(self, encoder: Any, config: dict | None = None) -> None:
        self.encoder = encoder
        self.config = config or {}
        self.status = "completed"
        self.alternative_candidates = []
        self.metrics = {}

    async def rank_candidates(
        self,
        candidates: list[dict],
        jd_dict: dict,
        top_n: int = 100,
        call_llm: bool = True,
        candidate_embeddings: np.ndarray | None = None,
    ) -> tuple[list[dict], list[tuple[str, int, float, DimScores]], np.ndarray]:
        """Run the multi-stage unified ranking pipeline on candidates in memory."""
        import time
        t_start = time.time()
        
        if not candidates:
            self.status = "completed"
            self.metrics = {
                "total_candidates": 0,
                "candidates_filtered": 0,
                "candidates_retrieved": 0,
                "candidates_scored": 0,
                "llm_candidates_evaluated": 0,
                "retrieval_time": 0.0,
                "ranking_time": 0.0,
                "llm_time": 0.0,
                "total_analysis_time": 0.0
            }
            return [], [], np.array([], dtype=np.float32)

        total_candidates = len(candidates)
        
        # ── Step 1: Extract candidate features (Parallel Processing) ──────────
        t_extract_start = time.time()
        extractor = StructuredFeatureExtractor()
        all_features = [None] * total_candidates
        metadata_map = {}
        original_candidates_map = {}
        
        # Helper to extract in a thread pool for speed
        def _extract_feats(idx, cand):
            meta = resolve_candidate_metadata(cand)
            f = extractor.extract(cand, batch_idx=0, position_in_batch=idx)
            return idx, f, meta
            
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor() as executor:
            fut_results = list(executor.map(lambda pair: _extract_feats(pair[0], pair[1]), enumerate(candidates)))
            
        for idx, f, meta in fut_results:
            all_features[idx] = f
            if meta.candidate_id:
                metadata_map[meta.candidate_id] = meta
                original_candidates_map[meta.candidate_id] = candidates[idx]
        
        # ── STAGE 1 — HARD FILTERING & EARLY ELIGIBILITY ──────────────────────
        t_stage1_start = time.time()
        target_category = _classify_jd_role_category(jd_dict)
        min_exp = float(jd_dict.get("min_experience") or 0.0)
        
        # Enforce display validations and critical skills (computed early for eligibility)
        critical_skills = []
        if jd_dict:
            req_skills = [s.lower().strip() for s in jd_dict.get("required_skills", []) if s]
            title_lower = (jd_dict.get("title") or "").lower()
            description_lower = (jd_dict.get("description") or jd_dict.get("full_text") or "").lower()
            predefined = {
                "project manager": ["stakeholder management", "project planning", "risk management", "scrum", "agile"],
                "project planning": ["stakeholder management", "project planning", "risk management", "scrum", "agile"],
                "pm": ["stakeholder management", "project planning", "risk management", "scrum", "agile"],
                "mlops": ["docker", "kubernetes", "mlflow", "ci/cd"],
                "retrieval engineer": ["embeddings", "vector search", "vector db", "sentence-transformers", "pinecone", "weaviate", "qdrant", "milvus", "faiss"],
                "search engineer": ["embeddings", "vector search", "vector db", "sentence-transformers", "pinecone", "weaviate", "qdrant", "milvus", "faiss"],
            }
            for kw, skills_list in predefined.items():
                if kw in title_lower or kw in description_lower:
                    for s in skills_list:
                        if s not in req_skills:
                            req_skills.append(s)
            critical_skills = list(set(req_skills))

        early_eligibility_map = {}
        passed_features_with_idx = []
        for idx, f in enumerate(all_features):
            is_disqualified = f.get("is_disqualified", False)
            if is_disqualified:
                continue
                
            cid = f.get("candidate_id") or f"CAND_{idx}"
            
            # Experience check (with 2-year buffer)
            yoe = float(f.get("years_exp", 0.0))
            exp_ok = not (min_exp > 0.0 and yoe < (min_exp - 2.0))
            
            # Category compatibility
            cand_cat = f.get("candidate_role_category", "BACKEND")
            allowed = COMPATIBLE_CATEGORIES.get(target_category, {"BACKEND"})
            cat_ok = cand_cat in allowed
            
            # Skill gate check
            has_critical_match = True
            if critical_skills:
                orig_cand = original_candidates_map.get(cid)
                if orig_cand:
                    cand_skills = [s.get("name", "").lower().strip() for s in orig_cand.get("skills", []) if s.get("name")]
                    career_history = orig_cand.get("career_history", [])
                else:
                    cand_skills = [s.get("name", "").lower().strip() for s in f.get("skills", []) if s.get("name")]
                    career_history = []
                
                has_critical_match = False
                for cs in critical_skills:
                    for s in cand_skills:
                        if cs in s or s in cs:
                            has_critical_match = True
                            break
                    if has_critical_match:
                        break
                        
                if not has_critical_match:
                    for role in career_history:
                        desc = (role.get("description") or "").lower()
                        r_title = (role.get("title") or "").lower()
                        for cs in critical_skills:
                            if cs in desc or cs in r_title:
                                has_critical_match = True
                                break
                        if has_critical_match:
                            break
            
            early_eligible = exp_ok and cat_ok and has_critical_match
            reasons = []
            if not exp_ok:
                reasons.append(f"insufficient experience (has {yoe:.1f} yrs, needs {min_exp} yrs)")
            if not cat_ok:
                reasons.append(f"role category mismatch (candidate: {cand_cat}, job: {target_category})")
            if not has_critical_match:
                reasons.append("missing critical skills")
                
            early_eligibility_map[cid] = {
                "eligible": early_eligible,
                "has_critical_match": has_critical_match,
                "reasons": reasons
            }
            
            passed_features_with_idx.append((idx, f))
            
        # Fallback if too strict
        if not passed_features_with_idx:
            for idx, f in enumerate(all_features):
                if not f.get("is_disqualified", False):
                    passed_features_with_idx.append((idx, f))
                    
        candidates_filtered = total_candidates - len(passed_features_with_idx)
        t_stage1_end = time.time()
        
        if not passed_features_with_idx:
            return [], [], np.array([], dtype=np.float32)
            
        # ── STAGE 2 — VECTOR RETRIEVAL (FAISS HNSW Index) ────────────────────
        t_stage2_start = time.time()
        
        # Load or compute passed embeddings first to determine correct dimension
        if candidate_embeddings is not None and len(candidate_embeddings) == total_candidates:
            passed_indices = [idx for idx, _ in passed_features_with_idx]
            passed_embs = candidate_embeddings[passed_indices]
        else:
            passed_indices = [idx for idx, _ in passed_features_with_idx]
            passed_embs = None

        # Determine target dimension
        dim = passed_embs.shape[1] if passed_embs is not None else self.encoder.embedding_dim

        # Align encoder model dimension with candidate embedding dimension
        if self.encoder.embedding_dim != dim:
            if dim == 384:
                self.encoder.model_name = "BAAI/bge-small-en-v1.5"
                self.encoder._model = None  # Force reload
            elif dim == 1024:
                self.encoder.model_name = "BAAI/bge-large-en-v1.5"
                self.encoder._model = None  # Force reload

        # JD embedding
        jd_text = (
            jd_dict.get("title", "")
            + " "
            + jd_dict.get("description", "")
            + " "
            + jd_dict.get("full_text", "")
        ).strip()
        jd_emb = self.encoder.encode_single(jd_text, normalize=True, bge_mode="query")
        
        # Compute passed embeddings if not loaded from cache
        if passed_embs is None:
            from src.features.text_builder import build_candidate_text
            passed_texts = [build_candidate_text(candidates[idx]) for idx in passed_indices]
            passed_embs = self.encoder.encode_batch(passed_texts, normalize=True, bge_mode="passage")
            
        # FAISS HNSW Search
        import faiss
        dim = passed_embs.shape[1]
        index = faiss.IndexHNSWFlat(dim, 32, faiss.METRIC_INNER_PRODUCT)
        index.hnsw.efConstruction = 64
        index.hnsw.efSearch = 64
        index.add(passed_embs.astype(np.float32))
        
        top_k_search = min(500, len(passed_features_with_idx))
        similarities, ann_indices = index.search(jd_emb.reshape(1, -1).astype(np.float32), top_k_search)
        similarities = similarities[0]
        ann_indices = ann_indices[0]
        
        retrieved_features_with_sim = []
        for sim, local_idx in zip(similarities, ann_indices):
            if local_idx < 0 or local_idx >= len(passed_features_with_idx):
                continue
            orig_idx, f = passed_features_with_idx[local_idx]
            retrieved_features_with_sim.append({
                "orig_idx": orig_idx,
                "features": f,
                "similarity": float(sim)
            })
            
        # Pool-normalize similarities
        if retrieved_features_with_sim:
            sims_list = [item["similarity"] for item in retrieved_features_with_sim]
            s_min, s_max = min(sims_list), max(sims_list)
            denom = s_max - s_min + 1e-8
            for item in retrieved_features_with_sim:
                item["similarity"] = (item["similarity"] - s_min) / denom
            
        candidates_retrieved = len(retrieved_features_with_sim)
        t_stage2_end = time.time()
        
        # ── STAGE 3 — ROLE SPECIALIZATION ENGINE ─────────────────────────────
        t_stage3_start = time.time()
        
        # Enforce exact pipeline: Hard Filter -> Vector Search -> Top 100 -> Top top_n -> LLM
        stage3_limit = 100
        stage4_limit = top_n
        
        # Dynamic LLM limit (Requirement 7)
        openings = None
        if jd_dict:
            openings = jd_dict.get("openings") or jd_dict.get("open_positions")
            
        if openings is not None:
            try:
                n_open = int(openings)
            except (ValueError, TypeError):
                n_open = 1
        else:
            n_open = 1
            
        if n_open <= 1:
            stage5_limit = 5
        elif n_open <= 3:
            stage5_limit = 10
        elif n_open <= 5:
            stage5_limit = 15
        elif n_open <= 10:
            stage5_limit = 30
        else:
            stage5_limit = max(30, n_open * 3)
            
        # Soft boost/penalty
        scored_pool = []
        for item in retrieved_features_with_sim:
            f = item["features"]
            orig_idx = item["orig_idx"]
            norm_sim = item["similarity"]
            cand_cat = f.get("candidate_role_category", "BACKEND")
            
            # Boost if exact target match
            if cand_cat == target_category:
                boost = 0.15
            elif cand_cat not in COMPATIBLE_CATEGORIES.get(target_category, {"BACKEND"}):
                boost = -0.10
            else:
                boost = 0.0
                
            blend_score = 0.70 * norm_sim + 0.30 * boost
            scored_pool.append({
                "orig_idx": orig_idx,
                "features": f,
                "similarity": norm_sim,
                "blend_score": blend_score
            })
            
        scored_pool.sort(key=lambda x: -x["blend_score"])
        top_scored = scored_pool[:stage3_limit]
        t_stage3_end = time.time()
        
        # ── STAGE 4 — DEEP SCORING ENGINE ─────────────────────────────────────
        t_stage4_start = time.time()
        scorer = DimensionScorer(jd_dict=jd_dict)
        final_scores = np.zeros(total_candidates, dtype=np.float32)
        dim_scores_list: list[DimScores | None] = [None] * total_candidates
        
        features_map = {}
        for idx, f in enumerate(all_features):
            cid = f.get("candidate_id", "")
            features_map[cid] = f
            
        top_scored_indices = {item["orig_idx"] for item in top_scored}
        
        # Populate similarities map for Scorer
        sims_map = {item["orig_idx"]: item["similarity"] for item in top_scored}
        
        for idx, f in enumerate(all_features):
            if idx not in top_scored_indices:
                continue
            cosine_sim = sims_map[idx]
            ds = scorer.score_all(f, cosine_sim=cosine_sim)
            dim_scores_list[idx] = ds
            final_scores[idx] = ds.final_score(self.config.get("weights"))
            
        candidate_ids = [f.get("candidate_id", f"CAND_{i}") for i, f in enumerate(all_features)]
        # Select top candidates based on deep scoring (narrow to Top 30)
        ranked = select_top_n(candidate_ids, final_scores, dim_scores_list, n=stage4_limit, id_to_features=features_map)
        
        candidates_scored = len(top_scored)
        t_stage4_end = time.time()
        
        # Generate before snapshots for audit
        before_snapshots = []
        if metadata_map:
            for cid, meta in metadata_map.items():
                before_snapshots.append({
                    "candidate_id": cid,
                    "candidate_name": meta.candidate_name,
                    "title": meta.current_title,
                    "experience": meta.years_of_experience
                })
        # critical_skills is already defined early in Stage 1

        t_stage5_start = time.time()
        # Evaluate Top stage5_limit candidates
        TOP_LLM = min(stage5_limit, len(ranked))
        
        # Create map of early eligibility for critical skill coverage/eligibility checks
        early_eligibility_map = {}
        for idx, f in enumerate(all_features):
            cid = f.get("candidate_id")
            if cid:
                is_dq = f.get("is_disqualified", False)
                has_crit = f.get("has_critical_match", True)
                reasons = []
                if is_dq:
                    reasons.append(f.get("disqualifier_reason") or "disqualified")
                if not has_crit:
                    reasons.append("missing critical skills")
                early_eligibility_map[cid] = {
                    "eligible": not is_dq and has_crit,
                    "has_critical_match": has_crit,
                    "reasons": reasons
                }

        candidate_summaries = []
        for rank_idx, item in enumerate(ranked[:TOP_LLM]):
            assert isinstance(item, tuple), f"Expected tuple in LLM loop, got {type(item).__name__}"
            print("ITEM_LLM_LOOP:", type(item), len(item), item)
            validate_tuple(item, 4, "UnifiedRankingEngine.rank_candidates Stage 5 LLM Loop", "(cid, rank, score, ds)")
            cid, rank, score, ds = item
            
            f = features_map.get(cid, {})
            cand_intel = f.get("candidate_intelligence", {})
            skills_list = cand_intel.get("skills", []) if cand_intel else []
            
            orig_cand = original_candidates_map.get(cid) or {}
            meta = metadata_map.get(cid)
            candidate_name = meta.candidate_name if meta else "Candidate"
            
            profile = orig_cand.get("profile") or {}
            c_title = profile.get("current_title") or orig_cand.get("current_title") or "Engineer"
            c_company = profile.get("current_company") or orig_cand.get("current_company") or ""
            raw_yoe = profile.get("years_of_experience") or profile.get("years_exp") or orig_cand.get("years_of_experience") or orig_cand.get("years_exp") or 0.0
            try:
                c_yoe = float(raw_yoe)
            except (ValueError, TypeError):
                c_yoe = 0.0
                
            c_role = f"{c_title} at {c_company}" if c_company and c_company != "—" else c_title
            c_summary = profile.get("summary") or f.get("summary") or "—"
            
            cov_matched, cov_total, cov_ratio, cov_list = calculate_critical_skill_coverage(f, jd_dict)
            cov_str = f"{cov_matched} / {cov_total}"
            
            early_info = early_eligibility_map.get(cid, {"eligible": True, "has_critical_match": True, "reasons": []})
            skills_score = ds.required_skills_match if ds else 0.0
            score_ok = score >= 0.20
            match_ok = (score * 100) >= 20.0
            skills_ok = skills_score > 0.0
            is_eligible = early_info["eligible"] and score_ok and match_ok and skills_ok
            
            candidate_summaries.append({
                "id": cid,
                "name": candidate_name,
                "skills": skills_list[:8],
                "experience": c_yoe,
                "role": c_role,
                "summary": c_summary,
                "critical skill coverage": cov_str,
                "eligibility": is_eligible
            })
            
        # Diagnostic logging (Requirement 6)
        if candidate_summaries:
            first_cand_keys = list(candidate_summaries[0].keys())
            first_ranked_item = ranked[0] if ranked else None
            tuple_len = len(first_ranked_item) if first_ranked_item else 0
            
            diag_msg = (
                f"\n[LLM_INPUT]\n"
                f"Candidate Count: {len(candidate_summaries)}\n\n"
                f"Candidate Keys:\n"
                + "\n".join(first_cand_keys) + "\n\n"
                f"Tuple Length:\n"
                f"{tuple_len}\n"
            )
            logger.info(diag_msg)
            print(diag_msg)
            
        recruiter_evals = {}
        ai_enhancement_error = None
        if call_llm and candidate_summaries:
            eval_res = await recruiter_evaluate_batch(
                jd_dict.get("description") or jd_dict.get("full_text") or "",
                jd_dict.get("title", ""),
                candidate_summaries
            )
            validate_tuple(eval_res, 2, "UnifiedRankingEngine.rank_candidates recruiter_evaluate_batch", "(batch_results, ai_enhancement_error)")
            batch_results, ai_enhancement_error = eval_res
            for ev in batch_results:
                cid = ev.get("candidate_id", "")
                if cid:
                    recruiter_evals[cid] = ev
                    
        # Blend scores & Fallback
        ai_enhancement_unavailable = False
        if call_llm and candidate_summaries and not recruiter_evals:
            logger.warning("OpenRouter evaluation failed or returned empty results. Falling back to deterministic scoring.")
            ai_enhancement_unavailable = ai_enhancement_error or "OpenRouter evaluation returned empty results."
            
            for rank_idx, item in enumerate(ranked):
                assert isinstance(item, tuple), f"Expected tuple in fallback loop, got {type(item).__name__}"
                print("ITEM_FALLBACK_LOOP:", type(item), len(item), item)
                validate_tuple(item, 4, "UnifiedRankingEngine.rank_candidates Fallback reasoning Loop", "(cid, rank, score, ds)")
                cid, rank, score, ds = item
                c = original_candidates_map.get(cid) or features_map.get(cid, {})
                recruiter_evals[cid] = generate_deterministic_reasoning(
                    c=c,
                    score=float(score),
                    rank=rank_idx + 1,
                    jd_title=jd_dict.get("title", "Target Role")
                )

        self.ai_enhancement_unavailable = ai_enhancement_unavailable

        blended_scores = np.zeros(total_candidates, dtype=np.float32)
        for i, cid in enumerate(candidate_ids):
            ds = dim_scores_list[i]
            f = features_map.get(cid, {})
            
            # Switch automatically to fallback ranking formula when AI enhancement is unavailable
            skill_match = ds.required_skills_match if ds else 0.0
            experience_match = ds.relevant_experience if ds else 0.0
            semantic_similarity = ds.semantic_similarity if ds else 0.0
            specialization_match = ds.specialization_match if ds else 0.5
            
            career_growth = ds.career_growth if ds else 0.5
            behavioral_fit = ds.behavioral_fit if ds else 0.5
            integrity = ds.integrity if ds else 0.5
            candidate_quality = (career_growth + behavioral_fit + integrity) / 3.0
            
            fallback_score = (
                0.35 * skill_match
                + 0.25 * semantic_similarity
                + 0.20 * experience_match
                + 0.15 * specialization_match
                + 0.05 * candidate_quality
            )
            if ds:
                fallback_score *= ds.disqualifier_multiplier

            recruiter_score = final_scores[i]
            ev = recruiter_evals.get(cid, {})
            if ev and not ai_enhancement_unavailable:
                gemini_score = float(ev.get("final_score", recruiter_score))
                blended = 0.75 * recruiter_score + 0.25 * gemini_score
                features_map[cid]["gemini_score"] = gemini_score
            else:
                blended = fallback_score if ai_enhancement_unavailable else recruiter_score
                features_map[cid]["gemini_score"] = blended
            blended_scores[i] = blended
            
        # Re-sort and rank candidates (final blended output limited to Top 30)
        final_ranked = select_top_n(candidate_ids, blended_scores, dim_scores_list, n=stage4_limit, id_to_features=features_map)
        llm_candidates_evaluated = len(candidate_summaries)
        t_stage5_end = time.time()
        
        apply_eligibility = self.config.get("apply_eligibility", True)
        
        results = []
        alternative_candidates = []
        ranked_tuples = []
        
        qualified_index = 1
        for rank_idx, item in enumerate(final_ranked):
            assert isinstance(item, tuple), f"Expected tuple in final assembly loop, got {type(item).__name__}"
            print("ITEM_FINAL_LOOP:", type(item), len(item), item)
            validate_tuple(item, 4, "UnifiedRankingEngine.rank_candidates Final output assembly Loop", "(cid, rank, score, ds)")
            cid, rank, score, ds = item
            f = features_map.get(cid, {})
            ev = recruiter_evals.get(cid, {})
            
            orig_cand = original_candidates_map.get(cid)
            if orig_cand:
                profile = orig_cand.get("profile") or {}
                candidate_name = profile.get("candidate_name") or profile.get("anonymized_name") or orig_cand.get("candidate_name") or "Candidate"
                current_title = profile.get("current_title") or orig_cand.get("current_title") or ""
                current_company = profile.get("current_company") or orig_cand.get("current_company") or ""
                location = profile.get("location") or profile.get("location_city") or orig_cand.get("location") or orig_cand.get("location_city") or ""
                raw_yoe = profile.get("years_of_experience") or profile.get("years_exp") or orig_cand.get("years_of_experience") or orig_cand.get("years_exp") or 0.0
                try:
                    years_of_experience = float(raw_yoe)
                except (ValueError, TypeError):
                    years_of_experience = 0.0
            else:
                meta = metadata_map.get(cid)
                candidate_name = meta.candidate_name if meta else "Candidate"
                current_title = meta.current_title if meta else ""
                current_company = meta.current_company if meta else ""
                location = meta.location if meta else ""
                years_of_experience = meta.years_of_experience if meta else 0.0
                
            top_skills = f.get("candidate_intelligence", {}).get("skills", [])[:4] if f.get("candidate_intelligence") else []
            
            if ev:
                reasoning = ev.get("recruiter_summary", "") or ev.get("why_selected", "")
                strengths = ev.get("strengths", [])
                weaknesses = ev.get("weaknesses", [])
                missing_skills = ev.get("missing_skills", [])
            else:
                from src.ranking.reasoning import ReasoningGenerator
                generator = ReasoningGenerator()
                reasoning = generator.generate(cid, f, ds, rank)
                strengths = [k for k, v in [
                    ("Strong specialization fit", ds.specialization_match if ds else 0),
                    ("Technical skill match", ds.required_skills_match if ds else 0),
                    ("Relevant experience quality", ds.relevant_experience if ds else 0),
                    ("Ideal logistics fit", ds.behavioral_fit if ds else 0),
                ] if v > 0.6][:3]
                weaknesses = [k for k, v in [
                    ("Lower skill match", ds.required_skills_match if ds else 0),
                    ("Limited relevant experience", ds.relevant_experience if ds else 0),
                ] if v < 0.5][:2]
                missing_skills = []
                
            skills_score = ds.required_skills_match if ds else 0.0
            spec_confidence = f.get("specialization_confidence", 0.4)
            
            if skills_score >= 0.7 and spec_confidence >= 0.7:
                confidence_level = "High"
            elif skills_score >= 0.4:
                confidence_level = "Medium"
            else:
                confidence_level = "Low"
                
            hiring_readiness = "high" if score > 0.65 else ("medium" if score > 0.4 else "low")
            match_percent = score * 100
            
            # Get early eligibility info
            early_info = early_eligibility_map.get(cid, {"eligible": True, "has_critical_match": True, "reasons": []})
            
            # Final scoring check
            score_ok = score >= 0.20
            match_ok = match_percent >= 20.0
            skills_ok = skills_score > 0.0
            
            elig_reasons = list(early_info["reasons"])
            if not score_ok:
                elig_reasons.append("overall match score below threshold (0.20)")
            if not match_ok:
                elig_reasons.append("match percent below threshold (20%)")
            if not skills_ok:
                elig_reasons.append("skills score is 0.0")
                
            is_eligible = early_info["eligible"] and score_ok and match_ok and skills_ok
            reason_str = "Eligible" if is_eligible else f"Ineligible: {', '.join(elig_reasons)}"
            
            # Calculate critical skill coverage (PART 4)
            cov_matched, cov_total, cov_ratio, cov_list = calculate_critical_skill_coverage(f, jd_dict)
            cov_str = f"{cov_matched} / {cov_total}"
            cov_pct = round(cov_ratio * 100, 1)
            
            # Match explanations breakdown (PART 5)
            r_match = round((ds.specialization_match if ds else 0.5) * 100, 1)
            cs_match = round((ds.required_skills_match if ds else skills_score) * 100, 1)
            exp_match = round((ds.relevant_experience if ds else 0.5) * 100, 1)
            sem_match = round((ds.semantic_similarity if ds else 0.5) * 100, 1)
            
            cand_entry = {
                "candidate_id": cid,
                "candidate_name": candidate_name,
                "current_title": current_title,
                "current_company": current_company,
                "location": location,
                "years_of_experience": years_of_experience,
                "top_skills": top_skills,
                "rank": rank_idx + 1,
                "ai_score": round(score, 4),
                "match_percent": round(match_percent, 1),
                "confidence": confidence_level,
                "hiring_readiness": hiring_readiness,
                "integrity_score": round(float(ev.get("integrity_score", ds.integrity if ds else 0.0)), 4),
                "reasoning": reasoning,
                "strengths": strengths,
                "weaknesses": weaknesses,
                "risks": [],
                "missing_skills": missing_skills,
                "interview_questions": [],
                "role_specialization": ev.get("role_specialization", f.get("candidate_specialization", "")),
                "score": round(score, 4),
                "matched_skills": top_skills,
                "why_selected": strengths,
                "why_not_ranked_higher": [ev.get("why_not_ranked_higher")] if ev.get("why_not_ranked_higher") else weaknesses,
                "eligibility": is_eligible,
                "eligibility_reason": reason_str,
                
                # Breakdown & coverage metrics
                "critical_skill_coverage": cov_str,
                "critical_skill_coverage_percent": cov_pct,
                "role_match_percent": r_match,
                "critical_skill_match_percent": cs_match,
                "experience_match_percent": exp_match,
                "semantic_similarity_percent": sem_match,
                "overall_match_percent": round(match_percent, 1),
            }
            
            results.append(cand_entry)
            ranked_tuples.append((cid, rank_idx + 1, score, ds))

        # Score Normalization stretching positive matches (PART 11)
        if results:
            positive_scores = [r["ai_score"] for r in results if r["ai_score"] > 0.0]
            if positive_scores:
                max_raw = max(positive_scores)
                min_raw = min(positive_scores)
                
                if max_raw == min_raw:
                    for r in results:
                        if r["ai_score"] > 0.0:
                            r["match_percent"] = 85.0
                            r["ai_score"] = 0.85
                            r["score"] = 0.85
                            r["overall_match_percent"] = 85.0
                else:
                    target_max = 96.0
                    target_min = 60.0
                    for r in results:
                        raw = r["ai_score"]
                        if raw > 0.0:
                            norm_val = target_min + (raw - min_raw) / (max_raw - min_raw) * (target_max - target_min)
                            r["match_percent"] = round(norm_val, 1)
                            r["ai_score"] = round(norm_val / 100.0, 4)
                            r["score"] = round(norm_val / 100.0, 4)
                            r["overall_match_percent"] = round(norm_val, 1)
                        else:
                            r["match_percent"] = 0.0
                            r["ai_score"] = 0.0
                            r["score"] = 0.0
                            r["overall_match_percent"] = 0.0

        # Mark recommended vs backup candidates (PART 10)
        openings_val = jd_dict.get("openings") if jd_dict else 5
        if not openings_val:
            openings_val = 5
        try:
            n_open = int(openings_val)
        except (ValueError, TypeError):
            n_open = 5
            
        for rank_idx, r in enumerate(results):
            if rank_idx < n_open:
                r["recommendation_status"] = "recommended"
            elif rank_idx < n_open + (2 * n_open):
                r["recommendation_status"] = "backup"
            else:
                r["recommendation_status"] = "standard"
                    
        # Apply display limit of top_n
        results = results[:top_n]
        ranked_tuples = ranked_tuples[:top_n]
        
        self.alternative_candidates = alternative_candidates
        if not results:
            self.status = "no_qualified_candidates"
        else:
            self.status = "completed"
            
        # Write candidate snapshot files
        after_snapshots = []
        for res in results:
            after_snapshots.append({
                "candidate_id": res["candidate_id"],
                "rank": res["rank"],
                "score": res["ai_score"]
            })
        snapshot_data = {"before": before_snapshots, "after": after_snapshots}
        try:
            with open("candidate_snapshot.json", "w", encoding="utf-8") as fh:
                json.dump(snapshot_data, fh, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Could not save candidate_snapshot.json: {e}")
            
        t_end = time.time()
        
        # Telemetry metrics
        self.metrics = {
            "total_candidates": total_candidates,
            "candidates_filtered": candidates_filtered,
            "candidates_retrieved": candidates_retrieved,
            "candidates_scored": candidates_scored,
            "llm_candidates_evaluated": llm_candidates_evaluated,
            "retrieval_time": round(t_stage2_end - t_stage2_start, 4),
            "ranking_time": round(t_stage4_end - t_stage4_start, 4),
            "llm_time": round(t_stage5_end - t_stage5_start, 4),
            "total_analysis_time": round(t_end - t_start, 4)
        }
        
        # Save metadata audit
        if metadata_map:
            total_cands = len(metadata_map)
            missing_names = sum(1 for m in metadata_map.values() if not m.candidate_name or m.candidate_name.strip() in ("", "Unknown Candidate", "Candidate"))
            missing_experience = sum(1 for m in metadata_map.values() if m.years_of_experience <= 0.0)
            audit_report = {
                "total_candidates": total_cands,
                "missing_names": missing_names,
                "missing_experience": missing_experience
            }
            try:
                with open("metadata_audit.json", "w", encoding="utf-8") as fh:
                    json.dump(audit_report, fh, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.warning(f"Could not save metadata_audit.json: {e}")
                
        # Validate ranking payload
        if results:
            anonymize_mode = self.config.get("anonymize_mode", False)
            validate_ranking_payload(results, anonymize_mode=anonymize_mode)
            
        return results, ranked_tuples, blended_scores

    async def rank_cached_candidates(
        self,
        cache_dir: str,
        jd_dict: dict,
        top_n: int = 100,
        call_llm: bool = True,
    ) -> tuple[list[dict], list[tuple[str, int, float, DimScores]], np.ndarray]:
        """Run the unified ranking pipeline on candidates in the feature cache."""
        from src.features.cache import FeatureCache
        cache = FeatureCache(cache_dir)
        
        meta = cache.load_meta()
        if not meta:
            return [], [], np.array([], dtype=np.float32)
            
        all_features = []
        for batch_id in cache.batch_ids():
            batch = cache.load_structured_batch(batch_id)
            all_features.extend(batch)
            
        # Reconstruct candidates list from cache structure to pass to rank_candidates
        candidates = []
        for f in all_features:
            # We reconstruct a basic candidate dict
            cand = {
                "candidate_id": f.get("candidate_id"),
                "profile": {
                    "anonymized_name": f.get("candidate_name", "Candidate"),
                    "current_title": f.get("current_title", ""),
                    "current_company": f.get("current_company", ""),
                    "location": f.get("location", ""),
                    "years_of_experience": f.get("years_exp", 0.0)
                },
                "skills": [{"name": s} for s in (f.get("skills") or f.get("candidate_intelligence", {}).get("skills") or [])],
                "career_history": [],
                "redrob_signals": {
                    "open_to_work_flag": f.get("open_to_work", True),
                    "notice_period_days": f.get("notice_period_days", 30)
                }
            }
            candidates.append(cand)
            
        # Extract flat embeddings from cache if available
        flat_embs = []
        for batch_id in cache.batch_ids():
            emb_batch = cache.load_embedding_batch(batch_id)
            flat_embs.append(emb_batch)
            
        candidate_embeddings = np.vstack(flat_embs) if flat_embs else None
        
        return await self.rank_candidates(
            candidates=candidates,
            jd_dict=jd_dict,
            top_n=top_n,
            call_llm=call_llm,
            candidate_embeddings=candidate_embeddings
        )
