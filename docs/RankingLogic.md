# Ranking Logic — Complete Scoring Methodology

**Version**: 1.0  
This document is the authoritative specification for all scoring weights, formulas, sub-scoring rubrics, normalization strategies, and tie-breaking rules.

---

## 1. Final Score Formula

```
final_score = (
    0.30 × semantic_skill_fit    +
    0.25 × experience_quality    +
    0.15 × career_progression    +
    0.15 × behavioral_signals    +
    0.10 × logistics_fit         +
    0.05 × profile_integrity
) × hard_disqualifier_multiplier
```

All dimension scores are in `[0.0, 1.0]` after normalization.  
`hard_disqualifier_multiplier ∈ {0.0, 1.0}` — any triggered disqualifier zeroes the entire score.

---

## 2. Pre-Scoring: Hard Disqualifiers

Hard disqualifiers are evaluated **before** any dimension scoring. Disqualified candidates receive `final_score = 0.0` immediately.

### 2.1 Honeypot Detection

**Flag 1: Impossible Tenure** (definitive disqualifier)
```python
def check_tenure_impossible(career_history: list) -> bool:
    for role in career_history:
        start_year = parse_date(role["start_date"]).year
        company_max_age_months = (DATASET_REFERENCE_YEAR - start_year) * 12
        if role["duration_months"] > company_max_age_months + 12:  # +12 tolerance
            return True
    return False
```

**Flag 2: Expert Skill with Zero Duration** (definitive disqualifier)
```python
def check_expert_zero_duration(skills: list) -> bool:
    for skill in skills:
        if skill["proficiency"] == "expert" and skill.get("duration_months", 1) == 0:
            return True
    return False
```

**Flag 3: Skills-to-Experience Ratio** (suspicion → disqualifier if severe)
```python
def check_skills_ratio(skills: list, years_exp: float) -> float:
    expert_advanced = sum(1 for s in skills if s["proficiency"] in ["expert", "advanced"])
    ratio = expert_advanced / max(years_exp, 1.0)
    if ratio > 2.0:
        return 1.0   # definitive honeypot
    elif ratio > 1.5:
        return 0.5   # suspicion weight
    return 0.0
```

**Flag 4: Extreme Title–Description Mismatch** (suspicion signal)
```python
NON_TECHNICAL_TITLES = {"marketing manager", "accountant", "graphic designer", 
                         "content writer", "sales executive", "hr manager",
                         "operations manager", "customer support"}
TECHNICAL_DESC_KEYWORDS = {
    "vector database", "embedding", "transformer", "neural network",
    "machine learning", "deep learning", "faiss", "pinecone", "qdrant",
    "sentence-transformers", "fine-tun", "llm", "bert", "gpt"
}

def check_title_desc_mismatch(career_history: list) -> int:
    mismatch_count = 0
    for role in career_history:
        title_lower = role["title"].lower()
        desc_lower = role["description"].lower()
        is_non_tech_title = any(t in title_lower for t in NON_TECHNICAL_TITLES)
        is_tech_desc = sum(1 for k in TECHNICAL_DESC_KEYWORDS if k in desc_lower) >= 3
        if is_non_tech_title and is_tech_desc:
            mismatch_count += 1
    return mismatch_count  # ≥3 → definitive honeypot
```

**Honeypot decision rule**:
```python
def is_honeypot(candidate) -> bool:
    if check_tenure_impossible(candidate["career_history"]):
        return True
    if check_expert_zero_duration(candidate["skills"]):
        return True
    ratio_score = check_skills_ratio(candidate["skills"], candidate["profile"]["years_of_experience"])
    mismatch_count = check_title_desc_mismatch(candidate["career_history"])
    if ratio_score == 1.0 or mismatch_count >= 3:
        return True
    suspicion = ratio_score + (0.3 if mismatch_count >= 2 else 0)
    return suspicion >= 1.0
```

### 2.2 Consulting-Only Career

```python
CONSULTING_FIRMS = {
    "tcs", "tata consultancy services", "wipro", "infosys", "accenture",
    "cognizant", "capgemini", "hcl technologies", "hcl", "tech mahindra",
    "mphasis", "hexaware", "niit technologies", "ltimindtree", "mindtree",
    "l&t infotech", "igate", "mastech"
}

def is_consulting_only(career_history: list) -> bool:
    if not career_history:
        return False
    for role in career_history:
        company_norm = role["company"].lower().strip()
        if not any(cf in company_norm for cf in CONSULTING_FIRMS):
            return False  # At least one non-consulting role found
    return True  # Every single role is at a consulting firm
```

### 2.3 Non-Technical Career with No AI History

```python
HARD_DISQUALIFIER_TITLES = {
    "accountant", "chartered accountant", "ca", "graphic designer",
    "ui designer", "ux designer", "content writer", "copywriter",
    "civil engineer", "mechanical engineer", "structural engineer",
    "hr manager", "human resources", "recruiter", "talent acquisition",
    "customer support", "customer service", "call center", "sales executive",
    "sales manager", "retail manager"
}

AI_TITLE_KEYWORDS = {
    "ml", "machine learning", "ai", "artificial intelligence", "data scientist",
    "data engineer", "nlp", "computer vision", "deep learning", "research engineer",
    "software engineer", "backend engineer", "platform engineer", "mlops",
    "analytics engineer", "quantitative", "algorithm"
}

def is_non_technical_no_ai(candidate) -> bool:
    # Check if current title is hard-disqualifier territory
    current = candidate["profile"]["current_title"].lower()
    is_current_nontechnical = any(t in current for t in HARD_DISQUALIFIER_TITLES)
    if not is_current_nontechnical:
        return False
    
    # Check if any career_history entry has AI/ML title
    for role in candidate["career_history"]:
        title_lower = role["title"].lower()
        if any(k in title_lower for k in AI_TITLE_KEYWORDS):
            return False  # They have AI history → don't disqualify
    
    return True  # Non-technical title + no AI career history → disqualify
```

---

## 3. Dimension 1: Semantic & Skill Fit (Weight: 30%)

### 3.1 Sub-components

```
semantic_skill_fit = (
    0.40 × semantic_similarity_score      +
    0.35 × skill_depth_score              +
    0.15 × core_jd_skill_coverage_score   +
    0.10 × assessment_score_boost
) × keyword_stuffing_penalty × llm_recency_penalty
```

### 3.2 Semantic Similarity Score

```python
# Pre-compute once: JD embedding
jd_text = build_jd_text()  # See §3.2.1 below
jd_embedding = model.encode([jd_text], normalize_embeddings=True)[0]  # [384]

# Per candidate:
candidate_text = build_candidate_text(candidate)  # See §3.2.2 below
candidate_embedding = model.encode([candidate_text], normalize_embeddings=True)[0]

semantic_similarity = float(np.dot(jd_embedding, candidate_embedding))
# Cosine similarity in [-1, 1]; for normalized vectors, always in [-1, 1]
# After normalization across pool: map to [0, 1]
```

**Normalization across pool**:
```python
# Normalize to [0, 1] using min-max after computing all similarities
sims = np.array([all candidate similarities])
sim_normalized = (sims - sims.min()) / (sims.max() - sims.min() + 1e-8)
```

#### 3.2.1 JD Text Construction

```python
JD_TEXT = """
Senior AI Engineer founding team role requiring production experience with embedding-based retrieval 
using sentence-transformers BGE E5 OpenAI embeddings handling embedding drift index refresh 
retrieval quality regression. Must have production vector database hybrid search experience with 
Pinecone Weaviate Qdrant Milvus OpenSearch Elasticsearch FAISS. Strong Python code quality. 
Hands-on evaluation framework design for ranking systems NDCG MRR MAP offline online correlation 
A/B test interpretation. Nice to have LLM fine-tuning LoRA QLoRA PEFT learning-to-rank XGBoost 
neural ranker HR-tech recruiting marketplace product experience distributed systems large-scale 
inference optimization open source contributions AI ML. Product company experience not consulting 
services. Deployed end-to-end ranking search recommendation system real users at scale.
""".strip()
```

The JD text is deliberately keyword-enriched and paraphrased to maximize embedding coverage of the semantic space relevant to the role.

#### 3.2.2 Candidate Text Construction

```python
def build_candidate_text(candidate: dict) -> str:
    parts = []
    
    # Profile headline and summary (most recent self-description)
    parts.append(candidate["profile"].get("headline", ""))
    parts.append(candidate["profile"].get("summary", ""))
    
    # Career descriptions — weight recent roles more heavily
    history = sorted(candidate["career_history"], 
                     key=lambda r: r["start_date"], reverse=True)
    for i, role in enumerate(history[:5]):  # Top 5 most recent roles
        weight_prefix = "Currently: " if i == 0 else ""
        parts.append(f"{weight_prefix}{role['title']} at {role['company']}: {role['description']}")
    
    # Skill names (just names for semantic coverage)
    skill_names = " ".join(s["name"] for s in candidate["skills"])
    parts.append(skill_names)
    
    return " ".join(filter(None, parts))[:4096]  # Truncate to 4096 chars
```

### 3.3 Skill Depth Score

```python
# JD Must-Have skill clusters
JD_MUST_HAVE_SKILLS = {
    "embedding_retrieval": [
        "sentence-transformers", "sentence transformers", "bge", "e5", 
        "openai embeddings", "dense retrieval", "bi-encoder", "cross-encoder",
        "semantic search", "embedding search"
    ],
    "vector_database": [
        "pinecone", "weaviate", "qdrant", "milvus", "faiss", 
        "opensearch", "elasticsearch", "vector search", "hybrid search", "ann"
    ],
    "python_advanced": ["python"],
    "evaluation_framework": [
        "ndcg", "mrr", "map", "mean average precision", "precision@k",
        "learning to rank", "ltr", "ranker", "ranking evaluation", 
        "offline evaluation", "a/b test", "a/b testing"
    ]
}

JD_NICE_TO_HAVE_SKILLS = {
    "llm_finetuning": ["lora", "qlora", "peft", "fine-tuning", "fine-tune", "finetuning"],
    "ltr_models": ["xgboost rank", "lambdamart", "listwise", "pairwise", "neural ranker"],
    "ml_core": ["pytorch", "tensorflow", "transformers", "hugging face", "scikit-learn"],
    "infra": ["kubernetes", "docker", "mlops", "ray", "triton", "onnx", "quantization"]
}

def compute_skill_depth_score(candidate: dict) -> float:
    skills = candidate["skills"]
    assessments = candidate["redrob_signals"]["skill_assessment_scores"]
    
    PROFICIENCY_WEIGHTS = {"beginner": 0.25, "intermediate": 0.5, "advanced": 0.75, "expert": 1.0}
    
    total_score = 0.0
    must_have_hits = 0
    
    for cluster_name, keywords in JD_MUST_HAVE_SKILLS.items():
        cluster_score = 0.0
        for skill in skills:
            skill_lower = skill["name"].lower()
            if any(kw in skill_lower for kw in keywords):
                prof_score = PROFICIENCY_WEIGHTS[skill["proficiency"]]
                duration_score = min(skill.get("duration_months", 0) / 24.0, 1.0)
                endorsement_score = min(np.log1p(skill["endorsements"]) / np.log(51), 1.0)
                
                # Check for platform assessment boost
                assessment_boost = 0.0
                for assess_key, assess_val in assessments.items():
                    if any(kw in assess_key.lower() for kw in keywords):
                        assessment_boost = (assess_val / 100.0) * 0.2
                        break
                
                raw_skill_score = prof_score * (0.6 + 0.4 * duration_score) * (1 + 0.3 * endorsement_score)
                cluster_score = max(cluster_score, raw_skill_score + assessment_boost)
        
        if cluster_score > 0:
            must_have_hits += 1
        total_score += cluster_score
    
    # Normalize: 4 must-have clusters
    skill_depth = total_score / len(JD_MUST_HAVE_SKILLS)
    
    # Nice-to-have boost (up to +0.1)
    nice_hits = 0
    for cluster_name, keywords in JD_NICE_TO_HAVE_SKILLS.items():
        for skill in skills:
            if any(kw in skill["name"].lower() for kw in keywords):
                nice_hits += 1
                break
    nice_boost = min(nice_hits / len(JD_NICE_TO_HAVE_SKILLS), 1.0) * 0.1
    
    return min(skill_depth + nice_boost, 1.0)
```

### 3.4 Core JD Skill Coverage Score

```python
def compute_core_coverage(candidate: dict) -> float:
    """How many of the 4 must-have skill clusters does the candidate cover?"""
    skills_lower = {s["name"].lower() for s in candidate["skills"]}
    
    coverage = 0
    for cluster_name, keywords in JD_MUST_HAVE_SKILLS.items():
        if any(any(kw in s for kw in keywords) for s in skills_lower):
            coverage += 1
    
    return coverage / len(JD_MUST_HAVE_SKILLS)  # 0.25 steps
```

### 3.5 Keyword Stuffing Penalty

```python
AI_BUZZWORDS = {
    "langchain", "rag", "retrieval augmented", "pinecone", "openai", "chatgpt",
    "gpt-4", "llama", "mistral", "vector database", "embedding", "langsmith",
    "crewai", "autogen", "llamaindex", "chroma", "weaviate"
}

def compute_keyword_stuffing_penalty(candidate: dict) -> float:
    skills_lower = [s["name"].lower() for s in candidate["skills"]]
    buzzword_count = sum(1 for s in skills_lower if any(b in s for b in AI_BUZZWORDS))
    
    if buzzword_count < 6:
        return 1.0  # No penalty
    
    # Check if descriptions back up the buzzwords
    all_descriptions = " ".join(r["description"].lower() for r in candidate["career_history"])
    PRODUCTION_EVIDENCE = ["deployed", "production", "real users", "a/b", "latency", "index", "serving"]
    production_hits = sum(1 for e in PRODUCTION_EVIDENCE if e in all_descriptions)
    
    # Check if titles are technical
    all_titles = [r["title"].lower() for r in candidate["career_history"]]
    technical_titles = sum(1 for t in all_titles if any(k in t for k in AI_TITLE_KEYWORDS))
    
    if production_hits < 2 and technical_titles == 0:
        return 0.4  # Strong keyword stuffing penalty
    elif production_hits < 2:
        return 0.7  # Mild penalty — has tech titles but weak description evidence
    
    return 1.0  # Has buzzwords AND production evidence — legitimate
```

### 3.6 LLM-Only Recency Penalty

```python
def compute_llm_recency_penalty(candidate: dict) -> float:
    """Detect candidates whose entire AI experience is <12 months old."""
    REFERENCE_DATE = datetime(2025, 1, 1)  # Use dataset reference date
    
    ai_exp_dates = []
    for role in candidate["career_history"]:
        desc_lower = role["description"].lower()
        has_ai = any(k in desc_lower for k in ["machine learning", "neural", "embedding", "model", "ml ", "ai "])
        if has_ai:
            start = parse_date(role["start_date"])
            ai_exp_dates.append(start)
    
    if not ai_exp_dates:
        return 1.0  # No AI experience found at all (different issue handled elsewhere)
    
    earliest_ai = min(ai_exp_dates)
    months_of_ai_exp = (REFERENCE_DATE - earliest_ai).days / 30.44
    
    if months_of_ai_exp < 12:
        return 0.7  # All AI experience is very recent
    elif months_of_ai_exp < 24:
        return 0.85  # Relatively new to AI
    
    return 1.0
```

---

## 4. Dimension 2: Experience Quality & Relevance (Weight: 25%)

```
experience_quality = (
    0.30 × years_exp_score           +
    0.35 × product_company_score     +
    0.25 × production_evidence_score +
    0.10 × tenure_stability_score
) × job_hop_penalty
```

### 4.1 Years of Experience Score

```python
def score_years_exp(years: float) -> float:
    if years < 3:
        return max(0.3, years / 3 * 0.5)  # 0.3–0.5 for <3 years
    elif years < 5:
        return 0.5 + (years - 3) / 2 * 0.2  # 0.5–0.7 for 3–5 years
    elif years <= 6:
        return 0.7 + (years - 5) * 0.2  # 0.7–0.9 for 5–6 years
    elif years <= 8:
        return 0.9 + (years - 6) / 2 * 0.1  # 0.9–1.0 for 6–8 years (sweet spot)
    elif years <= 10:
        return 1.0 - (years - 8) / 2 * 0.1  # 0.9–1.0 for 8–10 years
    elif years <= 15:
        return 0.9 - (years - 10) / 5 * 0.15  # 0.75–0.9 for 10–15 years
    else:
        return max(0.65, 0.75 - (years - 15) * 0.02)  # Tapering for 15+ years
```

### 4.2 Product Company Score

```python
def score_product_company_ratio(career_history: list) -> float:
    total_months = sum(r["duration_months"] for r in career_history)
    consulting_months = sum(
        r["duration_months"] for r in career_history
        if any(cf in r["company"].lower() for cf in CONSULTING_FIRMS)
    )
    
    if total_months == 0:
        return 0.5
    
    product_ratio = 1.0 - (consulting_months / total_months)
    
    # Score the ratio
    if product_ratio >= 0.9:
        return 1.0   # Almost entirely product
    elif product_ratio >= 0.7:
        return 0.85  # Mostly product with some consulting
    elif product_ratio >= 0.5:
        return 0.65  # Mixed
    elif product_ratio >= 0.3:
        return 0.45  # Mostly consulting with some product
    elif product_ratio > 0:
        return 0.25  # Mostly consulting
    else:
        return 0.0   # Handled by hard disqualifier
```

### 4.3 Production Evidence Score

```python
PRODUCTION_SIGNALS = {
    "strong": [
        "deployed to production", "in production", "serving production",
        "real users", "serving \\d+ users", "a/b test", "a/b testing",
        "latency sla", "p99 latency", "throughput", "requests per second",
        "index refresh", "embedding drift", "retrieval regression",
        "ndcg", "mrr", "offline evaluation", "online evaluation",
        "ranking system", "recommendation system", "search engine"
    ],
    "medium": [
        "deployed", "production system", "model serving", "inference pipeline",
        "model monitoring", "feature store", "end-to-end", "at scale",
        "high availability", "model pipeline"
    ],
    "weak": [
        "trained a model", "built a model", "ml model", "experimented",
        "prototyped", "poc", "proof of concept", "kaggle"
    ]
}

def score_production_evidence(career_history: list) -> float:
    all_desc = " ".join(r["description"].lower() for r in career_history)
    
    strong_hits = sum(1 for p in PRODUCTION_SIGNALS["strong"] if re.search(p, all_desc))
    medium_hits = sum(1 for p in PRODUCTION_SIGNALS["medium"] if p in all_desc)
    weak_hits = sum(1 for p in PRODUCTION_SIGNALS["weak"] if p in all_desc)
    
    score = min(1.0, (strong_hits * 0.20) + (medium_hits * 0.08) + (weak_hits * 0.02))
    return score
```

### 4.4 Tenure Stability Score (Anti-Job-Hop)

```python
def score_tenure_stability(career_history: list) -> tuple[float, float]:
    """Returns (stability_score, job_hop_penalty)"""
    if len(career_history) <= 1:
        return 1.0, 1.0
    
    # Sort by start date, exclude current role
    past_roles = sorted(
        [r for r in career_history if not r["is_current"]],
        key=lambda r: r["start_date"]
    )
    
    if not past_roles:
        return 1.0, 1.0
    
    avg_tenure = sum(r["duration_months"] for r in past_roles) / len(past_roles)
    short_tenures = sum(1 for r in past_roles if r["duration_months"] < 18)
    
    # Stability score
    if avg_tenure >= 24:
        stability = 1.0
    elif avg_tenure >= 18:
        stability = 0.8
    elif avg_tenure >= 12:
        stability = 0.6
    else:
        stability = 0.4
    
    # Job-hop penalty: >4 short stints in recent 6 years
    recent_years = 6
    cutoff = datetime.now() - timedelta(days=recent_years * 365)
    recent_short = sum(
        1 for r in past_roles
        if parse_date(r["start_date"]) > cutoff and r["duration_months"] < 18
    )
    
    job_hop_penalty = 1.0 if recent_short <= 2 else (0.85 if recent_short <= 3 else 0.70)
    
    return stability, job_hop_penalty
```

---

## 5. Dimension 3: Career Progression & Leadership (Weight: 15%)

```
career_progression = (
    0.35 × seniority_trajectory_score +
    0.25 × company_growth_score       +
    0.25 × leadership_evidence_score  +
    0.15 × scope_ownership_score
) + seniority_trajectory_bonus - stagnation_penalty
```

### 5.1 Seniority Level Mapping

```python
SENIORITY_LEVELS = {
    1: ["junior", "associate", "entry", "trainee", "intern"],
    2: ["engineer", "analyst", "developer", "scientist"],  # no modifier = mid
    3: ["senior", "sr.", "sr "],
    4: ["lead", "tech lead", "technical lead", "staff"],
    5: ["principal", "architect", "distinguished"],
    6: ["director", "vp", "head of", "manager of engineering", "cto", "chief"]
}

def infer_seniority(title: str) -> int:
    title_lower = title.lower()
    for level in range(6, 0, -1):
        if any(kw in title_lower for kw in SENIORITY_LEVELS[level]):
            return level
    return 2  # Default to mid-level if no modifier found
```

### 5.2 Seniority Trajectory Score

```python
def score_seniority_trajectory(career_history: list) -> tuple[float, float]:
    """Returns (trajectory_score, trajectory_bonus)"""
    roles_sorted = sorted(career_history, key=lambda r: r["start_date"])
    levels = [infer_seniority(r["title"]) for r in roles_sorted]
    
    if len(levels) <= 1:
        return 0.5, 0.0
    
    # Check if non-decreasing (upward or flat)
    is_non_decreasing = all(levels[i] <= levels[i+1] for i in range(len(levels)-1))
    trajectory_bonus = 0.2 if is_non_decreasing else 0.0
    
    # Overall trajectory score
    current_level = levels[-1]
    starting_level = levels[0]
    level_gain = current_level - starting_level
    
    if level_gain >= 3:
        score = 1.0
    elif level_gain == 2:
        score = 0.85
    elif level_gain == 1:
        score = 0.7
    elif level_gain == 0:
        score = 0.5  # Same level throughout
    else:
        score = 0.3  # Downward trajectory
    
    return score, trajectory_bonus
```

### 5.3 Leadership Evidence Score

```python
LEADERSHIP_PATTERNS = [
    (r"led\s+(?:a\s+)?team\s+of\s+(\d+)", "team_lead"),
    (r"managed\s+(?:a\s+)?team\s+of\s+(\d+)", "team_manage"),
    (r"led\s+(\d+)\s+engineers?", "team_lead"),
    (r"technical\s+lead", "tech_lead"),
    (r"architected\s+(?:the|a)\s+\w+", "architected"),
    (r"owned\s+(?:the|a)\s+\w+\s+(?:system|platform|service)", "owned_system"),
    (r"mentored?\s+\w+", "mentored"),
    (r"cross[\s-]functional", "cross_functional"),
]

def score_leadership_evidence(career_history: list) -> float:
    all_desc = " ".join(r["description"].lower() for r in career_history)
    
    hits = 0
    max_team_size = 0
    for pattern, label in LEADERSHIP_PATTERNS:
        match = re.search(pattern, all_desc)
        if match:
            hits += 1
            if label in ("team_lead", "team_manage"):
                try:
                    size = int(match.group(1))
                    max_team_size = max(max_team_size, size)
                except:
                    pass
    
    base_score = min(hits / 4.0, 0.8)  # Up to 0.8 from hits
    team_size_bonus = min(max_team_size / 20.0, 0.2)  # Up to 0.2 for team size
    
    return min(base_score + team_size_bonus, 1.0)
```

---

## 6. Dimension 4: Behavioral Signals & Engagement (Weight: 15%)

```
behavioral_signals = (
    0.30 × hiring_readiness_score    +
    0.25 × recruiter_engagement_score +
    0.20 × platform_trust_score      +
    0.15 × github_activity_score_norm +
    0.10 × market_validation_score
)
```

### 6.1 Hiring Readiness Score

```python
def score_hiring_readiness(signals: dict, reference_date: datetime) -> float:
    # open_to_work: binary, 0.4 weight
    otw = 1.0 if signals["open_to_work_flag"] else 0.0
    
    # Recency of last activity: 0.3 weight
    last_active = parse_date(signals["last_active_date"])
    days_since = (reference_date - last_active).days
    if days_since <= 7:
        recency = 1.0
    elif days_since <= 30:
        recency = 0.8
    elif days_since <= 60:
        recency = 0.6
    elif days_since <= 90:
        recency = 0.4
    else:
        recency = 0.2
    
    # Notice period: 0.3 weight
    notice = signals["notice_period_days"]
    if notice <= 30:
        notice_score = 1.0
    elif notice <= 60:
        notice_score = 0.7
    elif notice <= 90:
        notice_score = 0.5
    else:
        notice_score = 0.3
    
    return 0.4 * otw + 0.3 * recency + 0.3 * notice_score
```

### 6.2 Recruiter Engagement Score

```python
def score_recruiter_engagement(signals: dict) -> float:
    response_rate = signals["recruiter_response_rate"]
    avg_response_hours = signals["avg_response_time_hours"]
    
    # Response time normalized to [0, 168h] (1 week)
    response_time_score = max(0.0, 1.0 - avg_response_hours / 168.0)
    
    return 0.6 * response_rate + 0.4 * response_time_score
```

### 6.3 Platform Trust Score

```python
def score_platform_trust(signals: dict) -> float:
    verified = [
        signals["verified_email"],
        signals["verified_phone"],
        signals["linkedin_connected"]
    ]
    return sum(verified) / 3.0
```

### 6.4 GitHub Activity Score

```python
def score_github_activity(signals: dict) -> tuple[float, float]:
    """Returns (normalized_score, effective_weight)"""
    raw = signals["github_activity_score"]
    if raw == -1:
        return 0.5, 0.05   # Neutral, reduced weight
    return raw / 100.0, 0.15  # Normalized, full weight
```

### 6.5 Market Validation Score

```python
def score_market_validation(signals: dict) -> float:
    saved = signals["saved_by_recruiters_30d"]
    # Log-normalized: 0 → 0, 1 → 0.30, 5 → 0.60, 10 → 0.77, 50 → 1.0
    return min(np.log1p(saved) / np.log(1 + 50), 1.0)
```

---

## 7. Dimension 5: Location & Logistics Fit (Weight: 10%)

```
logistics_fit = (
    0.50 × location_fit_score     +
    0.30 × notice_period_score    +
    0.20 × salary_alignment_score
)
```

### 7.1 Location Fit Score

```python
PREFERRED_CITIES = {"pune", "noida"}
ACCEPTABLE_CITIES = {"hyderabad", "mumbai", "delhi", "gurgaon", "gurugram", 
                      "bangalore", "bengaluru", "chennai", "kolkata", "ahmedabad"}

def score_location(candidate: dict) -> float:
    location = candidate["profile"]["location"].lower()
    country = candidate["profile"]["country"].lower()
    willing = candidate["redrob_signals"]["willing_to_relocate"]
    
    if any(city in location for city in PREFERRED_CITIES):
        return 1.0
    elif any(city in location for city in ACCEPTABLE_CITIES):
        return 0.85
    elif country == "india":
        return 0.7 if willing else 0.55
    else:
        # Outside India
        return 0.4 if willing else 0.2
```

### 7.2 Salary Alignment Score

```python
TARGET_SALARY_MIN = 25.0  # LPA
TARGET_SALARY_MAX = 55.0  # LPA

def score_salary_alignment(signals: dict) -> float:
    s_min = signals["expected_salary_range_inr_lpa"]["min"]
    s_max = signals["expected_salary_range_inr_lpa"]["max"]
    
    # Check overlap with target range
    overlap_start = max(s_min, TARGET_SALARY_MIN)
    overlap_end = min(s_max, TARGET_SALARY_MAX)
    
    if overlap_end <= overlap_start:
        # No overlap
        if s_max < TARGET_SALARY_MIN:
            return 0.5  # Candidate expects less → could negotiate up (not a deal-breaker)
        else:
            return 0.3  # Candidate expects too much
    
    # Calculate overlap fraction
    overlap = overlap_end - overlap_start
    candidate_range = s_max - s_min
    target_range = TARGET_SALARY_MAX - TARGET_SALARY_MIN
    
    overlap_fraction = overlap / min(candidate_range + 1, target_range)
    
    if overlap_fraction >= 0.7:
        return 1.0
    elif overlap_fraction >= 0.4:
        return 0.8
    else:
        return 0.6
```

---

## 8. Dimension 6: Profile Integrity (Weight: 5%)

```
profile_integrity = (
    0.50 × completeness_score      +
    0.33 × verification_composite  +
    0.17 × consistency_score
) - low_completeness_penalty
```

```python
def score_profile_integrity(candidate: dict) -> float:
    signals = candidate["redrob_signals"]
    
    # Completeness
    completeness = signals["profile_completeness_score"] / 100.0
    
    # Verification
    verification = (
        int(signals["verified_email"]) +
        int(signals["verified_phone"]) +
        int(signals["linkedin_connected"])
    ) / 3.0
    
    # Consistency: does years_of_experience match career_history?
    derived_years = sum(r["duration_months"] for r in candidate["career_history"]) / 12.0
    stated_years = candidate["profile"]["years_of_experience"]
    discrepancy = abs(derived_years - stated_years)
    consistency = 1.0 if discrepancy <= 2 else (0.7 if discrepancy <= 5 else 0.4)
    
    score = 0.50 * completeness + 0.33 * verification + 0.17 * consistency
    
    # Low completeness penalty
    if completeness < 0.4:
        score *= 0.8
    
    return min(score, 1.0)
```

---

## 9. Normalization Strategy

All dimension scores are computed as raw values in [0.0, 1.0] per the formulas above. Because the formulas are already bounded, no additional pool-level normalization is needed for dimension scores.

**Exception**: `semantic_similarity_score` (raw cosine similarity) is pool-normalized using min-max normalization after computing similarities for all 100K candidates. This is done in Phase 2 when all embeddings have been processed.

---

## 10. Tie-Breaking Rules

When two candidates have identical `final_score` values (within 1e-6 tolerance):
1. **Primary tiebreaker**: `behavioral_signals` score (descending) — more engaged candidate ranks higher
2. **Secondary tiebreaker**: `logistics_fit` score (descending) — more logistically convenient ranks higher
3. **Tertiary tiebreaker**: `experience_quality` score (descending)
4. **Final tiebreaker**: `candidate_id` lexicographic order (ascending) — deterministic

---

## 11. Score Calibration and Sanity Checks

After computing all final_scores, run these sanity checks before writing the CSV:

```python
def validate_scores(scores: dict[str, float], top_100: list[str]) -> list[str]:
    warnings = []
    
    # Check monotonic non-increasing
    top_scores = [scores[cid] for cid in top_100]
    if any(top_scores[i] < top_scores[i+1] for i in range(99)):
        warnings.append("CRITICAL: Scores not monotonically non-increasing!")
    
    # Check minimum score bar for top 10
    if top_scores[9] < 0.5:
        warnings.append(f"WARNING: Rank 10 score {top_scores[9]:.4f} is below 0.5 — possible calibration issue")
    
    # Check maximum score is reasonable
    if top_scores[0] > 0.99:
        warnings.append(f"WARNING: Rank 1 score {top_scores[0]:.4f} is suspiciously high")
    
    # Check candidate IDs are unique
    if len(set(top_100)) != 100:
        warnings.append("CRITICAL: Duplicate candidate IDs in top 100!")
    
    return warnings
```
