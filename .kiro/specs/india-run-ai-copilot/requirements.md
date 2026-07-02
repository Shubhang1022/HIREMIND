# Requirements Document

## Introduction

The AI Recruiter Copilot is a CPU-only, offline candidate ranking system designed for the India Run AI & Data Challenge (Redrob Hackathon). Given a pool of 100,000 candidates in JSONL format and a specific Job Description for a Senior AI Engineer (Founding Team) role at Redrob AI, the system must semantically evaluate and rank the top 100 candidates, producing a submission CSV with candidate_id, rank, score, and reasoning columns. The system must run in ≤5 minutes on CPU-only hardware with ≤16 GB RAM and zero external network calls.

This system is explicitly NOT a keyword matcher or ATS. It must understand what the JD means — inferring intent, detecting anti-patterns, identifying honeypot candidates, and rewarding genuine production AI experience over buzzword stuffing.

## Glossary

- **Ranker**: The end-to-end candidate ranking system
- **Candidate**: A JSON object from candidates.jsonl conforming to candidate_schema.json
- **JD**: Job Description for Senior AI Engineer — Founding Team at Redrob AI
- **Semantic Fit**: Relevance of a candidate's experience to the JD evaluated by meaning, not keywords alone
- **Honeypot**: A candidate with an intentionally impossible or internally inconsistent profile inserted to detect naive systems
- **Keyword Stuffer**: A candidate with relevant AI keywords in skills/headline but mismatched career history or titles
- **Hard Disqualifier**: A condition that sets a candidate's final score to zero regardless of other dimensions
- **Redrob Signals**: The 23 behavioral and platform engagement signals in the redrob_signals object
- **Feature Store**: In-memory or disk-backed intermediate storage of computed per-candidate features
- **Pre-computation**: Offline phase that computes embeddings and structured features before ranking
- **Reasoning**: A concise, fact-grounded natural language explanation for a candidate's rank and score
- **Consulting-Only Career**: A career spent entirely at TCS, Wipro, Infosys, Accenture, Cognizant, or Capgemini with no product company experience
- **Production ML**: Machine learning models or systems deployed to serve real end-users, not experimental or academic work

## Requirements

### Requirement 1: Candidate Ingestion and Validation

**User Story:** As a ranking system, I want to read and validate all 100,000 candidates from a JSONL file, so that only structurally valid candidates proceed to scoring.

#### Acceptance Criteria

1. THE Ranker SHALL read candidates.jsonl line by line using streaming I/O to stay within 16 GB RAM
2. WHEN a line in candidates.jsonl cannot be parsed as valid JSON, THE Ranker SHALL skip that line and log a warning with the line number
3. WHEN a candidate record is missing a required field defined in candidate_schema.json, THE Ranker SHALL skip that candidate and log the candidate_id and missing field
4. THE Ranker SHALL complete ingestion of 100,000 candidates in ≤60 seconds on a standard CPU
5. WHEN ingestion completes, THE Ranker SHALL report the count of valid candidates, skipped lines, and validation errors

### Requirement 2: Honeypot Detection

**User Story:** As a ranking system, I want to detect and zero-score honeypot candidates before ranking, so that the submission is not disqualified for including more than 10 honeypot candidates in the top 100.

#### Acceptance Criteria

1. WHEN a candidate's total duration_months at a single company exceeds that company's possible existence duration inferred from start_date, THE Ranker SHALL flag that candidate as a honeypot
2. WHEN a candidate has a skill with proficiency="expert" and duration_months=0 for that skill, THE Ranker SHALL flag that candidate as a honeypot
3. WHEN a candidate has more than 8 skills at "expert" or "advanced" proficiency with 0 duration_months each, THE Ranker SHALL flag that candidate as a honeypot
4. WHEN a candidate's career_history title is in a non-technical domain (e.g., "Marketing Manager", "Accountant") but the description contains detailed technical implementation text (vector databases, transformer models, embedding pipelines), THE Ranker SHALL compute a title–description mismatch score and use it as a honeypot signal
5. WHEN a candidate's skills-to-experience ratio exceeds a threshold of 1.5 expert skills per year of experience, THE Ranker SHALL add a honeypot suspicion weight to that candidate's profile integrity score
6. IF a candidate is confirmed as a honeypot (≥2 impossibility flags), THEN THE Ranker SHALL set that candidate's final score to 0.0 and exclude them from the top-100 output

### Requirement 3: Hard Disqualifier Evaluation

**User Story:** As a ranking system, I want to automatically disqualify candidates who match explicit JD anti-patterns, so that clearly unfit candidates cannot appear in the top 100.

#### Acceptance Criteria

1. WHEN a candidate's entire career_history consists of roles only at consulting firms (TCS, Wipro, Infosys, Accenture, Cognizant, Capgemini, and equivalents), THE Ranker SHALL set that candidate's final score to 0.0
2. WHEN a candidate's current_title is in a clearly non-technical field (Accountant, Customer Support Representative, Graphic Designer, Content Writer, Civil Engineer, Mechanical Engineer, HR Manager) AND the candidate has no career_history entry with a technical AI/ML title, THE Ranker SHALL set that candidate's final score to 0.0
3. WHEN a candidate's AI/ML skill experience is entirely within the last 12 months with no pre-LLM production ML evidence, THE Ranker SHALL apply a 0.3 penalty multiplier to the semantic_skill_fit dimension
4. THE Ranker SHALL apply hard disqualifier evaluation before computing weighted dimension scores to avoid wasted computation

### Requirement 4: Semantic and Skill Fit Scoring

**User Story:** As a ranking system, I want to evaluate how well a candidate's actual experience matches what the JD means (not just what it says), so that genuine AI engineers are ranked above keyword stuffers.

#### Acceptance Criteria

1. THE Ranker SHALL compute semantic similarity between the candidate's concatenated career description text and the JD requirements using a locally loaded sentence-transformer model (no external API calls)
2. THE Ranker SHALL score presence and depth of must-have JD skills: embedding-based retrieval, vector databases, Python proficiency, and evaluation framework experience
3. WHEN computing skill depth, THE Ranker SHALL weight each skill by: proficiency level (beginner=0.25, intermediate=0.5, advanced=0.75, expert=1.0) × min(duration_months/24, 1.0) × (1 + endorsements/50)
4. WHEN a candidate's skill_assessment_scores contains a score for a JD-relevant skill, THE Ranker SHALL incorporate that score as a 0.2 weight boost to that skill's depth score
5. WHEN a candidate's skills list contains ≥6 AI/ML buzzwords but their career_history descriptions contain no evidence of production deployment, THE Ranker SHALL apply a keyword-stuffing penalty of 0.4 to the semantic_skill_fit score
6. THE Ranker SHALL assign a final semantic_skill_fit score in the range [0.0, 1.0] normalized across the candidate pool

### Requirement 5: Experience Quality and Relevance Scoring

**User Story:** As a ranking system, I want to evaluate career trajectory, company types, and evidence of production ML work, so that engineers with genuine product company experience rank above those from pure services backgrounds.

#### Acceptance Criteria

1. THE Ranker SHALL score years_of_experience against the JD target range of 5–9 years with a peak multiplier of 1.0 at 6–8 years, tapering to 0.7 below 5 years and 0.8 above 9 years
2. THE Ranker SHALL compute a product_company_ratio as the fraction of total career_months spent at non-consulting, non-services companies
3. WHEN a career_history description contains production deployment signals ("deployed to production", "real users", "A/B test", "latency", "throughput", "SLA"), THE Ranker SHALL increment the production_evidence_score for that candidate
4. WHEN a candidate has held the same title for more than 48 consecutive months with no company change, THE Ranker SHALL apply a 0.1 penalty to the career_progression dimension for stagnation
5. WHEN a candidate has changed companies more than 4 times in the last 6 years with title upgrades as the primary motivation pattern, THE Ranker SHALL apply a 0.15 penalty to the experience_quality score for title-chasing
6. THE Ranker SHALL assign a final experience_quality score in the range [0.0, 1.0] normalized across the candidate pool

### Requirement 6: Career Progression and Leadership Scoring

**User Story:** As a ranking system, I want to detect upward career trajectories, scope of ownership, and leadership signals, so that candidates who have grown and led teams rank higher than lateral movers.

#### Acceptance Criteria

1. THE Ranker SHALL infer title seniority levels (junior=1, mid=2, senior=3, lead=4, principal/staff=5, director+=6) from career_history titles
2. WHEN a candidate's title seniority sequence is strictly non-decreasing across their career_history, THE Ranker SHALL award a trajectory_bonus of 0.2 to career_progression
3. THE Ranker SHALL score company_size progression by rewarding candidates who have moved from smaller to larger companies over time
4. WHEN a career_history description contains leadership signals ("led a team of", "managed N engineers", "technical lead for", "architected"), THE Ranker SHALL extract and score team size and ownership scope
5. THE Ranker SHALL assign a final career_progression score in the range [0.0, 1.0] normalized across the candidate pool

### Requirement 7: Behavioral Signals and Engagement Scoring

**User Story:** As a ranking system, I want to use Redrob platform signals to assess a candidate's hiring readiness and platform engagement, so that active and responsive candidates rank higher than passive or disengaged ones.

#### Acceptance Criteria

1. THE Ranker SHALL compute a hiring_readiness_score as a weighted composite: open_to_work_flag (0.4 weight) + recency of last_active_date (0.3 weight, normalized to last 90 days) + inverted notice_period_days (0.3 weight)
2. THE Ranker SHALL compute a recruiter_engagement_score using recruiter_response_rate (0.6 weight) + inverted avg_response_time_hours normalized to 0–168h (0.4 weight)
3. THE Ranker SHALL compute a platform_trust_score as (verified_email + verified_phone + linkedin_connected) / 3
4. WHEN github_activity_score is not -1, THE Ranker SHALL include it as a 0.15 weight contribution to the behavioral_signals dimension
5. WHEN github_activity_score is -1 (no GitHub linked), THE Ranker SHALL treat the GitHub sub-score as neutral (0.5) and reduce its weight to 0.05
6. THE Ranker SHALL include saved_by_recruiters_30d as a market_validation signal with weight 0.1 (normalized via log scale to handle outliers)
7. THE Ranker SHALL assign a final behavioral_signals score in the range [0.0, 1.0] normalized across the candidate pool

### Requirement 8: Location and Logistics Fit Scoring

**User Story:** As a ranking system, I want to evaluate a candidate's location, relocation willingness, notice period, and salary alignment, so that logistically feasible hires rank higher.

#### Acceptance Criteria

1. THE Ranker SHALL score location_fit as: Pune or Noida = 1.0, Hyderabad/Mumbai/Delhi NCR = 0.85, other Tier-1 India city = 0.7, other India location = 0.5, outside India + willing_to_relocate = 0.4, outside India + not willing_to_relocate = 0.2
2. THE Ranker SHALL score notice_period as: 0–30 days = 1.0, 31–60 days = 0.7, 61–90 days = 0.5, 91–180 days = 0.3
3. THE Ranker SHALL score salary_alignment by checking whether the expected_salary_range_inr_lpa overlaps with the typical Series A Senior AI Engineer range of 25–55 LPA; full overlap = 1.0, partial overlap = 0.7, no overlap = 0.3
4. THE Ranker SHALL assign a final logistics_fit score in the range [0.0, 1.0] as a weighted composite of location_fit, notice_period, and salary_alignment

### Requirement 9: Profile Integrity Scoring

**User Story:** As a ranking system, I want to verify profile completeness and consistency signals, so that well-verified, complete profiles are rewarded and subtle inconsistencies are flagged.

#### Acceptance Criteria

1. THE Ranker SHALL normalize profile_completeness_score from [0, 100] to [0.0, 1.0] as a sub-component of profile_integrity
2. THE Ranker SHALL compute verification_composite as (verified_email + verified_phone + linkedin_connected) / 3
3. WHEN a candidate's profile_completeness_score is below 40, THE Ranker SHALL apply a 0.2 penalty to the profile_integrity score
4. THE Ranker SHALL assign a final profile_integrity score in the range [0.0, 1.0]

### Requirement 10: Final Score Assembly and Top-100 Selection

**User Story:** As a ranking system, I want to combine all dimension scores into a final weighted score and select the top 100 candidates, so that the submission maximizes NDCG@10 and NDCG@50.

#### Acceptance Criteria

1. THE Ranker SHALL compute final_score = (0.30 × semantic_skill_fit + 0.25 × experience_quality + 0.15 × career_progression + 0.15 × behavioral_signals + 0.10 × logistics_fit + 0.05 × profile_integrity) × hard_disqualifier_multiplier
2. WHEN a hard disqualifier is triggered, THE Ranker SHALL set hard_disqualifier_multiplier to 0.0, making final_score = 0.0
3. WHEN no hard disqualifier is triggered, THE Ranker SHALL set hard_disqualifier_multiplier to 1.0
4. THE Ranker SHALL select the top 100 candidates by descending final_score
5. WHEN two candidates have identical final_scores (within 1e-6 tolerance), THE Ranker SHALL break ties using behavioral_signals score as the secondary sort key
6. THE Ranker SHALL assign integer ranks 1–100 to the selected candidates, where rank 1 has the highest final_score
7. THE Ranker SHALL verify that output scores are monotonically non-increasing with rank before writing the CSV

### Requirement 11: Reasoning Generation

**User Story:** As a ranking system, I want to generate a concise, grounded reasoning string for each of the top 100 candidates, so that Stage 4 manual review confirms no hallucination and correct use of candidate data.

#### Acceptance Criteria

1. THE Ranker SHALL generate a reasoning string for each top-100 candidate that references specific facts from that candidate's actual profile (years of experience, current title, top relevant skills, location, notice period)
2. THE Ranker SHALL ensure each reasoning string explicitly connects at least one candidate attribute to a specific JD requirement
3. WHEN a candidate has notable gaps or concerns relative to the JD (e.g., high notice period, outside India, consulting-heavy background), THE Ranker SHALL acknowledge the concern in the reasoning string
4. THE Ranker SHALL ensure no reasoning string references a skill, employer, or credential not present in the candidate's actual profile
5. THE Ranker SHALL vary reasoning structure across the 100 rows to avoid templating artifacts detectable by manual review
6. THE Ranker SHALL ensure reasoning tone and enthusiasm are consistent with the candidate's rank (rank 1 reasoning should be more positive than rank 90 reasoning)
7. THE Ranker SHALL limit each reasoning string to 300 characters to fit within CSV constraints and stay focused

### Requirement 12: Output File Generation

**User Story:** As a ranking system, I want to write a correctly formatted submission CSV, so that Stage 1 format validation passes without errors.

#### Acceptance Criteria

1. THE Ranker SHALL write a CSV file with exactly the columns: candidate_id, rank, score, reasoning in that order
2. THE Ranker SHALL write exactly 100 data rows with ranks 1 through 100, each exactly once
3. THE Ranker SHALL ensure all candidate_ids in the output exist in the input candidates.jsonl
4. THE Ranker SHALL format scores as floating point numbers rounded to 4 decimal places
5. THE Ranker SHALL ensure scores are monotonically non-increasing from rank 1 to rank 100
6. WHEN the output file already exists, THE Ranker SHALL overwrite it without prompting
7. THE Ranker SHALL write the CSV using UTF-8 encoding with a standard Unix line ending

### Requirement 13: Performance Constraints

**User Story:** As a ranking system, I want to complete the full ranking pipeline in ≤5 minutes on CPU-only hardware, so that the submission passes Stage 3 code reproduction in the Docker sandbox.

#### Acceptance Criteria

1. THE Ranker SHALL complete the full end-to-end ranking of 100,000 candidates in ≤300 seconds on a 4-core CPU machine with 16 GB RAM
2. THE Ranker SHALL operate without GPU, CUDA, or any GPU-specific libraries
3. THE Ranker SHALL make zero outbound network calls during ranking (no OpenAI, Anthropic, HuggingFace Hub live inference, or any external API)
4. THE Ranker SHALL use ≤16 GB RAM at peak during the ranking phase
5. THE Ranker SHALL use ≤5 GB of intermediate disk storage for pre-computed feature caches
6. WHERE pre-computation is used, THE Ranker SHALL support a two-phase run: precompute.py for offline feature extraction and rank.py for fast online ranking
7. THE Ranker SHALL use Python multiprocessing or batched vectorized operations to parallelize candidate scoring across available CPU cores
