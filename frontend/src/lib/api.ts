/**
 * Typed API client — thin fetch wrapper pointing to the FastAPI backend.
 */

const getApiBase = () => {
  const rawUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
  return rawUrl.endsWith('/api/v1') ? rawUrl : `${rawUrl.replace(/\/$/, '')}/api/v1`;
};
const BASE_URL = getApiBase();

export class ApiError extends Error {
  constructor(
    public status: number,
    public statusText: string,
    message: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const res = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(options?.headers ?? {}),
    },
    ...options,
  });

  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new ApiError(res.status, res.statusText, text);
  }

  return res.json() as Promise<T>;
}

// ── Candidate endpoints ──────────────────────────────────────────────────────

import type { Candidate, CandidateListResponse } from "@/types/candidate";

export const candidatesApi = {
  list: (params?: { page?: number; pageSize?: number; search?: string }) => {
    const qs = new URLSearchParams();
    if (params?.page !== undefined) qs.set("page", String(params.page));
    if (params?.pageSize !== undefined)
      qs.set("page_size", String(params.pageSize));
    if (params?.search) qs.set("search", params.search);
    const query = qs.toString() ? `?${qs}` : "";
    return request<CandidateListResponse>(`/candidates${query}`);
  },

  get: (candidateId: string) =>
    request<Candidate>(`/candidates/${candidateId}`),
};

// ── Ranking endpoints ────────────────────────────────────────────────────────

import type { RankingRun, RankingRunCreateRequest } from "@/types/ranking";

export const rankingApi = {
  triggerRun: (body: RankingRunCreateRequest) =>
    request<RankingRun>("/ranking/run", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getRun: (runId: string) => request<RankingRun>(`/ranking/${runId}`),
};

// ── Health endpoint ──────────────────────────────────────────────────────────

export const healthApi = {
  check: () => request<{ status: string }>("/health"),
};
