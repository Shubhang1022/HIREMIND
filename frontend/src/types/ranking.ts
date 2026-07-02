/**
 * TypeScript interfaces for ranking runs and candidate rankings.
 * Mirrors backend/app/models/ranking.py and schemas/ranking.py.
 */

export type RankingRunStatus = "pending" | "running" | "completed" | "failed";

/** Per-candidate dimension scores snapshot stored in the ranking run. */
export interface DimScores {
  semantic_skill_fit: number;
  experience_quality: number;
  career_progression: number;
  behavioral_signals: number;
  logistics_fit: number;
  profile_integrity: number;
  disqualifier_multiplier: number;
}

/** One ranked candidate entry within a RankingRun. */
export interface CandidateRank {
  candidate_id: string; // format: CAND_XXXXXXX
  rank: number; // 1–100
  score: number; // final weighted score, 4 decimal places
  reasoning: string; // ≤300 chars, fact-grounded
  dim_scores?: DimScores;
}

/** Aggregate statistics for a completed ranking run. */
export interface RankingRunStats {
  total_candidates_read: number;
  valid_candidates: number;
  honeypots_detected: number;
  consulting_only_disqualified: number;
  non_technical_disqualified: number;
  total_disqualified: number;
  candidates_scored: number;
  model_used: string;
  phase1_runtime_seconds?: number;
  phase2_runtime_seconds?: number;
  total_runtime_seconds?: number;
  score_stats?: {
    mean: number;
    std: number;
    min: number;
    max: number;
    top_100_min: number;
  };
}

/** A full ranking run record. */
export interface RankingRun {
  run_id: string;
  status: RankingRunStatus;
  created_at: string; // ISO datetime
  completed_at?: string | null;
  error_message?: string | null;
  config_snapshot?: Record<string, unknown>;
  stats?: RankingRunStats;
  top_100: CandidateRank[];
}

/** Request body for POST /ranking/run */
export interface RankingRunCreateRequest {
  candidates_path?: string;
  jd_path?: string;
  config_overrides?: Record<string, unknown>;
}
