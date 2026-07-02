/**
 * TypeScript interfaces matching the backend candidate schema.
 * Mirrors candidate_schema.json and backend/app/schemas/candidate.py.
 */

export type ProficiencyLevel = "beginner" | "intermediate" | "advanced" | "expert";

export interface Skill {
  skill_name: string;
  proficiency: ProficiencyLevel;
  duration_months: number;
  endorsements: number;
  skill_assessment_score?: number | null;
}

export interface CareerHistory {
  company_name: string;
  title: string;
  start_date: string; // ISO date string e.g. "2020-01"
  end_date: string | null; // null = current
  duration_months: number;
  description: string;
  is_current: boolean;
}

export interface Education {
  institution: string;
  degree: string;
  field_of_study: string;
  start_year: number;
  end_year: number | null;
}

export interface Certification {
  name: string;
  issuer: string;
  issue_date: string;
  expiry_date: string | null;
}

export interface Language {
  language: string;
  proficiency: string;
}

export interface RedrobSignals {
  open_to_work_flag: boolean;
  last_active_date: string; // ISO datetime
  notice_period_days: number;
  recruiter_response_rate: number; // 0.0–1.0
  avg_response_time_hours: number;
  verified_email: boolean;
  verified_phone: boolean;
  linkedin_connected: boolean;
  github_activity_score: number; // -1 if not linked
  saved_by_recruiters_30d: number;
  profile_completeness_score: number; // 0–100
  willing_to_relocate: boolean;
}

export interface Candidate {
  candidate_id: string; // format: CAND_XXXXXXX
  headline: string;
  summary: string;
  current_title: string;
  current_company: string;
  years_of_experience: number;
  location: string;
  expected_salary_range_inr_lpa: {
    min: number;
    max: number;
  };
  skills: Skill[];
  career_history: CareerHistory[];
  education: Education[];
  certifications: Certification[];
  languages: Language[];
  redrob_signals: RedrobSignals;
  created_at?: string;
  updated_at?: string;
}

export interface CandidateListResponse {
  items: Candidate[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}
