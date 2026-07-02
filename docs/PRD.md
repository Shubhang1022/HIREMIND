# Product Requirements Document — AI Recruiter Copilot

**Project**: India Run AI & Data Challenge — Redrob Hackathon  
**Role**: Senior AI Engineer — Founding Team at Redrob AI  
**Version**: 1.0  
**Status**: Draft — Pending Approval

---

## 1. Vision

Build an **AI Recruiter Copilot** — a system that thinks like a senior technical recruiter, not like an ATS.

A senior recruiter reading 100,000 resumes would:
- Understand what the JD *means*, not just what it *says*
- Recognize genuine production ML experience by reading descriptions, not just titles
- Penalize keyword stuffers and title-chasers instinctively
- Catch obviously impossible profiles before wasting time
- Weigh engagement and responsiveness alongside raw qualifications
- Give honest, specific reasoning for every decision

This system replicates that judgment at scale: 100,000 candidates evaluated in under 5 minutes on commodity hardware, producing a ranked list of the top 100 with human-readable reasoning grounded in actual candidate data.

---

## 2. Problem Statement

### 2.1 The Challenge

Redrob's recruiter network needs to fill a critical founding-team hire: **Senior AI Engineer** for an AI-native talent intelligence platform. The requirements are specific, opinionated, and deliberately anti-pattern-aware. The candidate pool is massive (100,000), noisy (title–description mismatches, keyword stuffers, honeypots), and diverse (45% from outside India).

Human review of 100,000 profiles is infeasible. Current ATS tools would fail because:
- They match keywords, not meaning
- They can't distinguish "deployed ML to production at scale" from "listed Python as a skill"
- They have no concept of anti-patterns (consulting-only careers, LLM-only experience, title-chasing)
- They can't evaluate behavioral signals (engagement, responsiveness, hiring readiness)

### 2.2 What This System Is NOT

| What it is NOT | Why this matters |
|---|---|
| Not an ATS | ATSs filter on keyword presence. This system evaluates semantic meaning. |
| Not a keyword matcher | A candidate without "LangChain" in skills but with 5 years of production vector search experience should rank above a candidate who listed every AI buzzword but worked in marketing. |
| Not a static rule engine | Rules help with hard disqualifiers, but the top ranking must emerge from multi-dimensional semantic evaluation. |
| Not a data copy machine | Generating reasoning by hallucinating skills or employers not in the profile is disqualifying in Stage 4 review. |

### 2.3 Success Criteria

The submission is scored against a hidden ground truth using:

| Metric | Weight | Meaning |
|---|---|---|
| NDCG@10 | 50% | Top 10 candidates must be highly relevant; order matters |
| NDCG@50 | 30% | Top 50 must be strong; diminishing order sensitivity |
| MAP | 15% | Average precision across all relevant candidates found |
| P@10 | 5% | Fraction of top 10 that are relevant |

**NDCG@10 dominates** (50% weight). Getting the top 10 right is more important than getting the order of ranks 50–100 right. Optimize the top of the list aggressively.

---

## 3. Hackathon Constraints (Hard Limits)

These constraints are non-negotiable — violation results in Stage 3 disqualification.

| Constraint | Limit | Implication |
|---|---|---|
| Total runtime | ≤ 5 minutes (300 seconds) wall-clock | Two-phase architecture: pre-compute offline, rank online |
| RAM | ≤ 16 GB | Streaming JSONL reader, batched embeddings, no full in-memory load |
| Compute | CPU only — no GPU | Use quantized/small embedding models, avoid torch GPU paths |
| Network | Offline — no external API calls | No OpenAI, Anthropic, HuggingFace Hub live inference |
| Disk | ≤ 5 GB intermediate storage | Numpy caches for embeddings, lightweight feature store |
| Honeypot rate | ≤ 10% of top 100 (max 10 honeypots) | Explicit detection logic is mandatory |

---

## 4. Core Evaluation Dimensions

The system evaluates candidates across **6 weighted dimensions**. Weights are designed to reflect what the JD actually cares about most.

### Dimension 1: Semantic & Skill Fit (30%)

**What it measures**: Does the candidate actually know the things the JD requires?

This is the primary differentiator between a genuine AI engineer and a keyword stuffer. It must:
- Read career descriptions semantically, not just match keywords
- Score depth of must-have skills (embedding retrieval, vector DBs, evaluation frameworks, Python)
- Penalize inflated skills sections not backed by description evidence
- Use locally loaded sentence-transformer embeddings for semantic similarity

**Key insight**: A Marketing Manager whose career descriptions detail implementing RAG pipelines and FAISS indices should score higher on this dimension than an ML Engineer whose descriptions are entirely about Excel dashboards.

### Dimension 2: Experience Quality & Relevance (25%)

**What it measures**: Is the candidate's experience actually relevant, at the right level, and from the right company types?

- Years of experience targeting 5–9 years (sweet spot 6–8)
- Product company ratio — what fraction of career was at product companies vs. consulting firms
- Evidence of production ML deployment extracted from description text
- Penalize job-hopping patterns and title-chasing behavior

### Dimension 3: Career Progression & Leadership (15%)

**What it measures**: Has the candidate grown over time, taken ownership, and led technical systems?

- Title seniority trajectory (upward = good, flat for 5+ years = weak signal)
- Company size progression (growing companies = growth mindset)
- Evidence of team leadership, system ownership, technical mentorship
- Scope signals: "architected the system" vs. "contributed a feature"

### Dimension 4: Behavioral Signals & Engagement (15%)

**What it measures**: Is the candidate actively seeking a new role, responsive, and trustworthy?

- Hiring readiness: open_to_work + last_active_date recency + notice period
- Recruiter engagement: response rate + response time
- Platform trust: email/phone verification + LinkedIn connection
- Market validation: saved by recruiters (proxy for external demand)
- GitHub activity (for AI engineers, active code = strong signal)

### Dimension 5: Location & Logistics Fit (10%)

**What it measures**: Is hiring this person operationally feasible?

- Preferred cities: Pune, Noida
- Acceptable: Hyderabad, Mumbai, Delhi NCR
- India elsewhere: acceptable with relocation consideration
- Outside India: possible with willing_to_relocate, but logistics cost
- Notice period and salary alignment with Series A Senior AI Engineer range

### Dimension 6: Profile Integrity (5%)

**What it measures**: Is the profile honest, consistent, and verifiable?

- Profile completeness
- Verification status (email, phone)
- Internal consistency checks
- Honeypot signals (this dimension feeds the hard disqualifier logic)

---

## 5. Anti-Pattern Detection Requirements

These patterns must be detected and penalized. They appear explicitly in the JD and in the submission spec.

### 5.1 Hard Disqualifiers (score → 0.0)

| Anti-Pattern | Detection Method |
|---|---|
| Honeypot profile | tenure_impossible OR expert+zero_duration flags (see DatasetAnalysis.md §5) |
| Consulting-only career | All career_history companies in {TCS, Wipro, Infosys, Accenture, Cognizant, Capgemini} |
| Non-technical career, no AI history | current_title + all career titles in non-technical domain set |

### 5.2 Soft Penalties (score multiplied down)

| Anti-Pattern | Penalty | Detection |
|---|---|---|
| Keyword stuffer | 0.4× semantic_skill_fit | ≥6 AI buzzwords in skills + mismatched descriptions/titles |
| LLM-only experience (<12 months AI) | 0.3 penalty on semantic_skill_fit | All AI experience dated within last 12 months; no pre-LLM ML evidence |
| Title-chaser | 0.15 penalty on experience_quality | >4 company changes in 6 years, each with title upgrade, short tenures |
| Long stagnation | 0.1 penalty on career_progression | Same title for >48 months with no meaningful scope change |
| CV/Speech/Robotics primary focus | 0.2 penalty on semantic_skill_fit | Primary skills/descriptions in CV, audio, robotics with no NLP/IR evidence |

---

## 6. Output Requirements

### 6.1 Submission CSV Format

```
candidate_id,rank,score,reasoning
CAND_0000042,1,0.8934,"6.8 yrs exp; ML Engineer at product startups; BGE/FAISS production deployments; Pune-based; 15d notice; strong assessment scores."
...
```

- Exactly 100 rows, ranks 1–100 each exactly once
- Scores monotonically non-increasing with rank
- All candidate_ids must exist in candidates.jsonl
- Scores rounded to 4 decimal places
- Reasoning: specific, factual, grounded, honest about gaps, no hallucination

### 6.2 Reasoning Quality Standards (Stage 4 Check)

Each reasoning string must pass the following checks:
1. References at least one specific fact from the candidate's actual profile
2. Connects at least one attribute to a specific JD requirement
3. Acknowledges notable concerns (if any exist)
4. Contains no fabricated skills, employers, or credentials
5. Varies in structure (not identical template for all 100)
6. Tone matches rank (rank 1 is enthusiastic; rank 90 is measured)
7. ≤300 characters (fits CSV cell without truncation)

### 6.3 Reasoning Templates (illustrative, must vary)

**Top 10 (strong match)**:
> "{yoe} yrs; {title} at {company_type}; evidence of {key_skill_1} and {key_skill_2} in production; {location}; {notice}d notice; response rate {response_rate:.0%}."

**Mid-range (11–50)**:
> "{yoe} yrs; {title}; strong {dimension_strength} but {gap_description}; {location}; {notice}d notice."

**Lower range (51–100)**:
> "{title}; {strength} noted; limited {primary_gap}; {notice}d notice; {location} — logistics feasible."

---

## 7. User Stories Summary

| # | As a... | I want to... | So that... |
|---|---|---|---|
| 1 | Ranking System | Read 100K candidates efficiently | I don't exceed 16 GB RAM |
| 2 | Ranking System | Detect and zero-score honeypots | The submission isn't disqualified |
| 3 | Ranking System | Apply hard disqualifiers before scoring | I don't waste compute on irrelevant candidates |
| 4 | Ranking System | Score semantic fit using local embeddings | I reward real AI experience over keyword stuffing |
| 5 | Ranking System | Evaluate experience quality and company type | I differentiate product engineers from services engineers |
| 6 | Ranking System | Score career progression and leadership | I identify candidates who've grown and owned systems |
| 7 | Ranking System | Use behavioral signals for engagement scoring | I favor active, responsive, hiring-ready candidates |
| 8 | Ranking System | Score location and logistics | I identify operationally feasible hires |
| 9 | Ranking System | Assemble final weighted score | I produce a fair, calibrated ranking |
| 10 | Ranking System | Generate grounded reasoning strings | Stage 4 manual review confirms no hallucination |
| 11 | Ranking System | Write valid submission CSV | Stage 1 format validation passes |
| 12 | Ranking System | Complete full pipeline in ≤5 min | Stage 3 code reproduction in Docker sandbox passes |

---

## 8. Non-Goals (Explicit Out of Scope)

- **No UI**: This is a CLI tool for hackathon submission, not a production product
- **No database**: All state is in-memory or in flat files; no SQL/NoSQL required
- **No model training**: Use pre-trained models only; no fine-tuning on this dataset
- **No batch API calls**: No external inference at any phase
- **No ATS features**: No resume parsing, job posting management, or applicant tracking
- **No multi-JD support**: The system is designed for exactly one JD (hardcoded or config-driven)
- **No real-time ranking**: This is a batch offline system, not a live API

---

## 9. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Embedding model too slow for 5-min budget | Medium | High | Use all-MiniLM-L6-v2 (smallest viable); pre-compute embeddings offline |
| Honeypot false positives (zeroing real candidates) | Low | High | Use strict flags only for definitive honeypots; suspicion scores are secondary |
| Keyword stuffer penalty over-applied | Medium | Medium | Require both high buzzword count AND mismatched descriptions before applying penalty |
| Top 10 dominated by behavioral signals | Medium | High | Cap behavioral dimension at 15% weight; semantic_skill_fit must be 30% |
| Reasoning hallucination (fabricated skills) | Low | High | Template-based generation using only extracted candidate fields |
| OOM during embedding pre-computation | Low | High | Batch encode in chunks of 512; stream candidates, don't load all at once |
