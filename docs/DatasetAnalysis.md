# Dataset Analysis — India Run AI & Data Challenge

> **Purpose**: Deep-dive reference document for all dataset files, schema details, statistical findings, data quality issues, and design implications for the AI Recruiter Copilot.

---

## 1. File Inventory

| File | Format | Size (est.) | Purpose |
|---|---|---|---|
| `candidates.jsonl` | JSONL (one JSON per line) | ~465 MB uncompressed | Full candidate pool — 100,000 records |
| `candidate_schema.json` | JSON Schema (draft-07) | ~8 KB | Authoritative schema for all candidate fields |
| `sample_candidates.json` | JSON Array | ~2 MB | First 50 candidates, pretty-printed for inspection |
| `sample_submission.csv` | CSV | ~8 KB | Reference output format: 100 rows, 4 columns |
| `job_description.docx` | DOCX | ~40 KB | Target JD: Senior AI Engineer — Founding Team at Redrob AI |
| `redrob_signals_doc.docx` | DOCX | ~25 KB | Signal definitions and behavioral context for all 23 signals |
| `submission_spec.docx` | DOCX | ~30 KB | Full rules: format, scoring, honeypot rules, evaluation stages |
| `README.docx` | DOCX | ~15 KB | Getting started guide |

**Key sizing note**: At ~465 MB uncompressed JSONL, reading line-by-line (streaming) is mandatory. Loading the entire file into memory as a Python list would require ~4–6 GB RAM (parsed Python dicts are ~10× raw bytes for deeply nested structures), leaving insufficient headroom under the 16 GB constraint when combined with embedding model weights (~1 GB for BGE-base).

---

## 2. Complete Schema Documentation

### 2.1 Top-Level Structure

```
Candidate (object)
├── candidate_id          string, required, pattern: CAND_[0-9]{7}
├── profile               object, required
├── career_history        array[1..10], required
├── education             array[0..5], required
├── skills                array[0..∞], required
├── certifications        array, optional
├── languages             array, optional
└── redrob_signals        object, required
```

### 2.2 Entity: profile

| Field | Type | Constraints | Notes |
|---|---|---|---|
| anonymized_name | string | required | Synthetic name, no PII |
| headline | string | required | One-line professional summary |
| summary | string | required | Multi-paragraph career narrative |
| location | string | required | City, state format |
| country | string | required | Country name |
| years_of_experience | number | 0–50, required | Self-reported float |
| current_title | string | required | Most recent job title |
| current_company | string | required | Current employer |
| current_company_size | enum | required | See size enum below |
| current_industry | string | required | Sector/industry string |

**Company size enum** (ordered): `1-10`, `11-50`, `51-200`, `201-500`, `501-1000`, `1001-5000`, `5001-10000`, `10001+`

**Design implication**: `years_of_experience` is self-reported and may not match sum of `career_history[].duration_months`. Always compute derived experience from career_history for scoring; use profile.years_of_experience as a secondary check.

### 2.3 Entity: career_history (array item)

| Field | Type | Constraints | Notes |
|---|---|---|---|
| company | string | required | Employer name |
| title | string | required | Job title at this role |
| start_date | date string | required | YYYY-MM-DD |
| end_date | date string or null | required | null if current role |
| duration_months | integer | ≥0, required | Pre-computed; verify against date diff |
| is_current | boolean | required | True if active role |
| industry | string | required | Industry of this company |
| company_size | enum | required | Same enum as profile |
| description | string | required | Free-text responsibilities and achievements |

**Critical field**: `description` is the richest signal for semantic scoring. It contains evidence of production deployment, team leadership, specific technologies, and scope of ownership. Some descriptions intentionally mismatch the title (dataset noise / honeypot signal).

**Duration verification**: `duration_months` should equal `ceil((end_date - start_date).days / 30.44)`. Mismatches > 3 months are suspicious. Tenure > company age is a honeypot flag.

### 2.4 Entity: education (array item)

| Field | Type | Constraints | Notes |
|---|---|---|---|
| institution | string | required | University or college name |
| degree | string | required | Degree type: B.Tech, M.Tech, Ph.D, MBA, etc. |
| field_of_study | string | required | CS, Data Science, ECE, etc. |
| start_year | integer | 1970–2030, required | Enrollment year |
| end_year | integer | 1970–2035, required | Graduation year |
| grade | string or null | optional | GPA, percentage, class |
| tier | enum | required | tier_1 / tier_2 / tier_3 / tier_4 / unknown |

**Tier mapping** (approximate):
- `tier_1`: IITs, IISc, BITS Pilani, NITs (top), IIMs, IIIT Hyderabad
- `tier_2`: State engineering colleges (top), VIT, SRM, Manipal, PSG
- `tier_3`: Mid-tier state colleges, private universities
- `tier_4`: Low-tier colleges, distance education
- `unknown`: Foreign institutions, unrecognized names

**Design implication**: Education tier is a weak signal for this JD; the role values production experience far more. Use tier as a minor tiebreaker, not a primary dimension. Penalize only the complete absence of a technical degree in edge cases.

### 2.5 Entity: skills (array item)

| Field | Type | Constraints | Notes |
|---|---|---|---|
| name | string | required | Skill label |
| proficiency | enum | required | beginner / intermediate / advanced / expert |
| endorsements | integer | ≥0, required | Platform endorsement count |
| duration_months | integer | ≥0, optional | Months used (0 is suspicious for expert claims) |

**Proficiency weights** for scoring: beginner=0.25, intermediate=0.5, advanced=0.75, expert=1.0

**Endorsement normalization**: log(1 + endorsements) / log(51) normalized to [0, 1] for endorsement counts up to 50.

**Critical integrity check**: `proficiency=expert` with `duration_months=0` is a honeypot flag. `proficiency=expert` with `duration_months < 6` is suspicious.

### 2.6 Entity: certifications (array item, optional)

| Field | Type | Constraints |
|---|---|---|
| name | string | required |
| issuer | string | required |
| year | integer | required |

**Relevance**: ~55% of candidates have no certifications. For this JD, AWS/GCP ML certifications, TensorFlow Developer, or PyTorch-related certs provide a modest signal boost. Certifications from unrecognized issuers are near-zero weight.

### 2.7 Entity: redrob_signals (object) — Full Signal Reference

| Signal | Type | Range | Missing sentinel | Design weight |
|---|---|---|---|---|
| profile_completeness_score | float | 0–100 | — | Low (0.05 dim) |
| signup_date | date | — | — | Negligible |
| last_active_date | date | — | — | High (recency) |
| open_to_work_flag | boolean | T/F | — | High |
| profile_views_received_30d | integer | ≥0 | — | Low |
| applications_submitted_30d | integer | ≥0 | — | Low |
| recruiter_response_rate | float | 0.0–1.0 | — | High |
| avg_response_time_hours | float | ≥0 | — | Medium |
| skill_assessment_scores | dict | 0–100 per skill | empty dict | Medium |
| connection_count | integer | ≥0 | — | Low |
| endorsements_received | integer | ≥0 | — | Low |
| notice_period_days | integer | 0–180 | — | High (logistics) |
| expected_salary_range_inr_lpa | {min, max} | 0–∞ | — | Medium |
| preferred_work_mode | enum | remote/hybrid/onsite/flexible | — | Medium |
| willing_to_relocate | boolean | T/F | — | High (logistics) |
| github_activity_score | float | -1–100 | -1 (no GitHub) | Medium |
| search_appearance_30d | integer | ≥0 | — | Low |
| saved_by_recruiters_30d | integer | ≥0 | — | Medium |
| interview_completion_rate | float | 0.0–1.0 | — | Medium |
| offer_acceptance_rate | float | -1–1.0 | -1 (no history) | Low |
| verified_email | boolean | T/F | — | Medium |
| verified_phone | boolean | T/F | — | Medium |
| linkedin_connected | boolean | T/F | — | Low |

---

## 3. Statistical Analysis (based on 500–1000 candidate sample)

### 3.1 Profile-Level Statistics

| Metric | Value |
|---|---|
| Total candidates | 100,000 |
| Avg years_of_experience | ~7.2 years |
| Median years_of_experience | ~6.5 years |
| Range years_of_experience | 0.5 – 20+ years |
| Candidates with 5–9 yrs experience | ~38% |
| Candidates with <3 yrs experience | ~15% |

### 3.2 Current Title Distribution (sample, top 17)

| Title | Approx % |
|---|---|
| ML Engineer / Senior ML Engineer | ~12% |
| AI Engineer | ~8% |
| Data Scientist | ~8% |
| Software Engineer (generic) | ~10% |
| Business Analyst | ~5% |
| Marketing Manager | ~4% |
| Operations Manager | ~4% |
| HR Manager | ~4% |
| Content Writer | ~4% |
| Customer Support | ~3% |
| Accountant | ~3% |
| Civil Engineer | ~3% |
| Graphic Designer | ~3% |
| Mechanical Engineer | ~3% |
| Project Manager | ~4% |
| Sales Executive | ~3% |
| Other/misc | ~21% |

**Implication**: Only ~28% of candidates hold an AI/ML-adjacent title. The remaining ~72% are non-technical or tangentially technical. The scoring system must handle large numbers of irrelevant candidates efficiently (hard disqualifiers should short-circuit computation for the ~40% who are clearly non-technical with no AI history).

### 3.3 Geography Distribution

| Country | Approx % |
|---|---|
| India | ~45% |
| USA | ~20% |
| Canada | ~10% |
| UK | ~8% |
| Australia | ~7% |
| Germany | ~5% |
| Singapore | ~3% |
| Other | ~2% |

**Within India cities**: Bangalore (~30%), Hyderabad (~20%), Pune (~15%), Delhi NCR (~15%), Mumbai (~10%), Chennai (~5%), Others (~5%)

**Logistics impact**: Only ~45% are in India at all. Of those, ~60% are in Tier-1 cities. The location scoring must not penalize India candidates too harshly vs overseas; the JD does accept relocation.

### 3.4 Skills Distribution

**Top 20 skills by frequency**: Python, SQL, Machine Learning, NLP, Deep Learning, TensorFlow, PyTorch, AWS, GCP, Azure, Data Analysis, Project Management, Excel, React, JavaScript, Feature Engineering, Kubernetes, Docker, Airflow, Spark

**JD-critical skills** (must-haves):
- sentence-transformers / BGE / E5 / OpenAI embeddings — low frequency, high signal
- Pinecone / Weaviate / Qdrant / Milvus / OpenSearch / FAISS — low-to-medium frequency
- NDCG / MRR / MAP / evaluation frameworks — very low frequency, very high signal
- Python (strong) — high frequency, but must verify depth via descriptions

**JD nice-to-haves**:
- LoRA / QLoRA / PEFT fine-tuning — low frequency, medium signal
- Learning-to-rank (LTR) / XGBoost-based rankers — medium frequency, medium signal

### 3.5 Education Tier Distribution

| Tier | Approx % |
|---|---|
| tier_1 | ~8% |
| tier_2 | ~18% |
| tier_3 | ~32% |
| tier_4 | ~38% |
| unknown | ~4% |

### 3.6 Redrob Signals Statistical Summary

| Signal | Mean | Median | Key Observation |
|---|---|---|---|
| profile_completeness_score | ~68% | ~70% | Wide distribution; ~8% below 40% |
| open_to_work_flag | ~36% True | — | Only 36K candidates are actively open |
| github_activity_score | — | — | ~46% have -1 (no GitHub) |
| notice_period_days | ~73 days | ~60 days | Range 0–180; avg ~2.5 months |
| recruiter_response_rate | ~0.45 | ~0.42 | Significant spread |
| offer_acceptance_rate | — | — | ~65% have -1 (no history) |
| skill_assessment_scores | — | empty | ~72% have empty dict |
| verified_email | ~52% | — | Just over half verified |
| verified_phone | ~49% | — | Nearly half verified |
| linkedin_connected | ~35% | — | Minority connected |
| saved_by_recruiters_30d | — | 0–2 | Highly skewed; top candidates 10+ |

### 3.7 Salary Range Distribution

- Range: 3–60 LPA (broad)
- Median: ~15–18 LPA
- Target range for JD (Series A Senior AI Engineer): 25–55 LPA
- ~25% of candidates are in or overlap the target range

### 3.8 Company Types

- Consulting-heavy (TCS/Wipro/Infosys/Accenture/Cognizant): ~20% of career history entries
- Pure consulting-only careers: estimated ~8% of candidates → hard disqualified
- Product companies (startup to scale-up): ~35% of career entries
- Hybrid (some consulting, some product): ~37% of career entries

---

## 4. Data Quality Issues

### 4.1 Title–Description Mismatches (Intentional Dataset Noise)

**Observation from sample_candidates**: Several candidates have `career_history[].title = "Marketing Manager"` but `description` contains detailed technical text about implementing transformer models, vector databases, or ML pipelines. This is intentional noise designed to test whether the system reads descriptions semantically vs. matching on title alone.

**Implication for ranking**: The scoring system MUST weight career_history `description` content more heavily than `title` for semantic evidence. A candidate who was titled "Marketing Manager" but whose description describes building a semantic search system should be evaluated based on the description.

**Distinction from honeypots**: Title–description mismatch alone is NOT a honeypot flag. Honeypots have *impossible* profiles (tenure > company age, expert + 0 duration). Mismatches are realistic career data that tests semantic reading. Only flag as honeypot when combined with other impossibility signals.

### 4.2 Self-Reported years_of_experience vs. Derived Experience

`profile.years_of_experience` is self-reported and may differ from `sum(career_history[].duration_months) / 12`. Common patterns:
- Self-reported is higher (candidate counts informal/freelance work)
- Gaps between roles not represented
- Self-reported includes education period

**Recommendation**: Use `min(profile.years_of_experience, derived_years_from_career_history)` as the conservative experience estimate for scoring. Flag large discrepancies (>3 years) as a minor integrity signal.

### 4.3 Missing Optional Fields

| Field | Missing Rate | Handling |
|---|---|---|
| certifications | ~55% | Treat as neutral (0.5 sub-score), not penalized |
| languages | ~20% | Ignore; not JD-relevant |
| skill.duration_months | ~5% | Default to proficiency-based estimate |
| grade (education) | ~30% | Ignore; tier is the signal |
| github_activity_score = -1 | ~46% | Treat as neutral, reduced weight |
| offer_acceptance_rate = -1 | ~65% | Exclude from scoring, don't penalize |
| skill_assessment_scores = {} | ~72% | Treat as neutral (no boost/penalty) |

### 4.4 Date Consistency Issues

**Observed patterns**:
- `end_date = null` is valid for current role; check `is_current = true` consistency
- Some `duration_months` values don't match date arithmetic exactly (off by 1–2 months — acceptable rounding)
- Future `last_active_date` values may appear (dataset uses synthetic future dates, e.g., 2025–2026)

**Recommendation**: Normalize all dates relative to the dataset's reference date (infer from `last_active_date` maximum). Use relative recency rather than absolute dates.

### 4.5 Skills List Quality

- Skill names are inconsistent in casing and format: "NLP", "nlp", "Natural Language Processing" may all appear
- Normalize skill names to lowercase for matching
- Some skills lists contain non-technical entries alongside technical ones (e.g., "Photoshop" next to "PyTorch")
- Very long skills lists (>20 skills) are common and require proper normalization

---

## 5. Honeypot Identification Methodology

The submission spec warns of ~80 honeypot candidates designed to detect naive ranking systems. Identifying them before ranking is critical — submissions with >10 honeypots in the top 100 are **disqualified**.

### 5.1 Impossible Tenure Flag

```
For each role in career_history:
    role_start = parse(start_date).year
    company_years_possible = current_year - min_company_founding_year
    if duration_months > company_years_possible * 12:
        flag as honeypot (tenure_impossible)
```

**Detection method**: Cross-reference company name against known founding years for major tech companies (Google, Pinecone, etc.). For synthetic company names, infer founding year from the earliest role at that company in the dataset.

### 5.2 Expert + Zero Duration Flag

```
For each skill in skills:
    if proficiency == "expert" AND duration_months == 0:
        flag as honeypot (expert_zero_duration)
```

This is the clearest honeypot signal. No legitimate expert should have 0 months of usage.

### 5.3 Skills-to-Experience Ratio Flag

```
expert_advanced_count = count(skills where proficiency in [expert, advanced])
years_exp = profile.years_of_experience
ratio = expert_advanced_count / max(years_exp, 1)
if ratio > 1.5:
    add honeypot_suspicion weight
```

A candidate with 3 years of experience cannot legitimately have 8 expert-level skills.

### 5.4 Title–Description Extreme Mismatch Flag

```
title_domain = classify_title(career_history[i].title)  # technical vs non-technical
desc_domain = classify_description(career_history[i].description)  # technical vs non-technical
if title_domain == NON_TECHNICAL and desc_domain == HIGH_TECHNICAL:
    mismatch_score += 1
if mismatch_score > 2 (across multiple roles):
    add honeypot_suspicion weight
```

Note: Single mismatch = likely realistic noise (career change, role evolution). Multiple mismatches across 3+ roles = honeypot.

### 5.5 Honeypot Decision Rule

```
if tenure_impossible OR expert_zero_duration:
    → definitive honeypot → final_score = 0.0
if (expert_zero_duration_count >= 3) OR (ratio > 2.0) OR (mismatch_score >= 3):
    → likely honeypot → final_score = 0.0
```

Be conservative: it's better to miss a honeypot than to zero-score a legitimate strong candidate. Use the strict flags (tenure_impossible, expert_zero_duration) as definitive; use suspicion weights as contributing factors only.

### 5.6 Keyword Stuffer Detection (NOT a honeypot, but a penalty)

Keyword stuffers are legitimate candidates who inflated their skills section:
- Skills list has ≥6 AI/ML buzzwords (RAG, LangChain, Pinecone, etc.)
- But current_title and all career_history titles are non-technical (e.g., Marketing Manager)
- And career_history descriptions show no evidence of ML implementation

These candidates should not score zero (they may have genuine adjacent skills) but should receive a significant penalty on semantic_skill_fit: multiply by 0.4 instead of zeroing out.

---

## 6. Behavioral Signal Interpretation Guide

### 6.1 Hiring Readiness Signals

**open_to_work_flag**: Binary, high weight. ~36% of candidates are open. This is the single strongest indicator of active job search.

**last_active_date recency**: Compute days since last login relative to dataset reference date. Score:
- 0–7 days: 1.0
- 8–30 days: 0.8
- 31–60 days: 0.6
- 61–90 days: 0.4
- >90 days: 0.2

**notice_period_days**: The JD explicitly states sub-30 ideal; can buy out up to 30 days. Score as described in Requirements.

### 6.2 Engagement Quality Signals

**recruiter_response_rate**: Best signal for engagement quality. A 0.7+ response rate indicates an actively engaged, professional candidate. A 0.1 response rate suggests the candidate ignores recruiter outreach.

**avg_response_time_hours**: Invert and normalize. Response within 4h = excellent. >48h = poor. Normalize to [0, 168h] range:
```
response_score = max(0, 1 - avg_response_time_hours / 168)
```

**interview_completion_rate**: Shows reliability. A rate < 0.5 is a concern. A rate of 1.0 is a strong positive signal.

### 6.3 Platform Validation Signals

**verified_email + verified_phone**: Basic identity validation. Both verified = 1.0; one verified = 0.67; neither = 0.0.

**linkedin_connected**: Only 35% connected. Mild positive signal for professional presence.

**github_activity_score**: For AI engineers, this is a strong quality signal. Score > 60 indicates active code contributions. -1 (no GitHub) is treated neutrally, not penalized (many strong engineers don't maintain public GitHub).

### 6.4 Market Validation Signals

**saved_by_recruiters_30d**: This is external validation from the recruiter community. Normalize with log scale:
```
market_score = min(log(1 + saved_by_recruiters_30d) / log(10), 1.0)
```
A candidate saved 10 times in 30 days is a strong market signal.

**profile_views_received_30d**: Secondary validation. High views + low saves = profile is being seen but not acted on (weak signal). High views + high saves = strong market signal.

### 6.5 Missing Signal Handling

| Signal | Missing condition | Fallback |
|---|---|---|
| github_activity_score = -1 | No GitHub linked | Use 0.5 (neutral), reduce weight to 0.05 |
| offer_acceptance_rate = -1 | No offer history | Exclude from scoring entirely |
| skill_assessment_scores = {} | No assessments taken | Skip assessment boost |

---

## 7. Key Insights for Ranking System Design

### 7.1 The Signal-to-Noise Challenge

Only ~15–20% of the 100,000 candidates will have genuinely relevant AI/ML backgrounds for this JD. The system needs to efficiently filter out the ~80% who are clearly non-relevant before doing expensive semantic scoring.

**Two-pass strategy**:
1. **Fast pre-filter** (O(N) linear): Apply hard disqualifiers and title-based filters to eliminate ~60% of candidates. This is cheap (no embeddings needed).
2. **Full semantic scoring** (on remaining ~40,000): Apply embedding-based similarity + structured scoring. This fits in 5 minutes if each candidate takes <0.45ms on average after pre-filtering.

### 7.2 Embedding Strategy Under CPU Constraint

Loading a full sentence-transformers model (MPNET-base, ~420MB) and running 100K inference calls sequentially is too slow. Recommended approach:
- Use a smaller model: `all-MiniLM-L6-v2` (~23MB, 384-dim) or `BGE-small-en-v1.5` (~24MB, 384-dim)
- Batch encode in chunks of 512 candidates
- Pre-compute all embeddings in `precompute.py` and cache to disk (numpy `.npy` files)
- At scoring time, load cached embeddings and compute cosine similarity via numpy (fast)

**Estimated pre-computation time**: ~3–4 minutes for 100K candidates with `all-MiniLM-L6-v2` on a 4-core CPU using sentence-transformers batch encoding.

### 7.3 The Sample Submission Tells You What NOT to Do

The provided `sample_submission.csv` is a deliberately bad example:
- Rank 1–13 are HR Managers, Content Writers, Accountants, Civil Engineers, and Marketing Managers
- These are clearly wrong for a Senior AI Engineer role
- This baseline represents naive ranking (likely by recruiter_response_rate + AI skill count only)
- The competition's ground truth will heavily penalize this

**Lesson**: Never sort primarily by behavioral signals or raw skill count. Semantic and experience fit must dominate the top of the ranking.

### 7.4 Geographic Distribution Creates Bias Risk

~55% of candidates are outside India. The JD preference is for India-based candidates. If location_fit is over-weighted, we risk missing strong candidates from the diaspora who are willing to relocate. Balance:
- Weight location at 10% of final score (not 20–25%)
- willing_to_relocate + strong technical fit can overcome location distance

### 7.5 Consulting-Only Career Detection

The top 6 consulting firms to flag: TCS, Wipro, Infosys, Accenture, Cognizant, Capgemini. Additional extended list: HCL, Tech Mahindra, Mphasis, Hexaware, NIIT Technologies.

```python
CONSULTING_FIRMS = {
    "tcs", "tata consultancy", "wipro", "infosys", "accenture",
    "cognizant", "capgemini", "hcl", "tech mahindra", "mphasis",
    "hexaware", "niit technologies", "ltimindtree", "l&t infotech"
}
def is_consulting_only(career_history):
    firms = {normalize(role.company) for role in career_history}
    return all(any(cf in firm for cf in CONSULTING_FIRMS) for firm in firms)
```

### 7.6 Production ML Evidence Vocabulary

Key phrases to scan in `career_history[].description` for production ML evidence:
- **Strong signals**: "deployed to production", "serving X requests/day", "A/B test", "real users", "latency SLA", "index refresh", "embedding drift", "retrieval quality", "NDCG", "MRR", "offline evaluation", "online evaluation"
- **Medium signals**: "model serving", "inference pipeline", "model monitoring", "feature store", "recommendation system", "ranking system", "search engine", "retrieval system"
- **Weak signals**: "trained a model", "built a model", "experimented with", "explored", "prototyped"
- **Negative signals**: "Kaggle competition", "side project", "tutorial", "blog post", "academic", "research paper only"

### 7.7 Scoring Calibration Expectations

Expected distribution of final_score for top-100 candidates:
- Rank 1–10: 0.75–0.95 (exceptional candidates — strong semantic fit + production experience + good signals)
- Rank 11–50: 0.55–0.75 (strong candidates with some gaps)
- Rank 51–100: 0.40–0.55 (competent candidates with notable gaps or logistics issues)

Candidates below 0.30 should not appear in top 100. If the scoring produces fewer than 100 candidates above 0.30, re-examine the calibration.
