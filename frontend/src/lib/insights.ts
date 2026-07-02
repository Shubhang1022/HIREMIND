import type { Candidate } from '@/lib/mockData';

export interface RecruiterInsights {
  topSkillGap: string;
  avgAiScore: number;
  openToWorkCount: number;
  highIntegrityCount: number;
  hiddenGems: Candidate[];
  highRisk: Candidate[];
  excellentMatches: number;
}

export function computeRecruiterInsights(candidates: Candidate[]): RecruiterInsights {
  const skillGaps = candidates.flatMap((c) => c.missingSkills);
  const gapCounts = skillGaps.reduce<Record<string, number>>((acc, skill) => {
    acc[skill] = (acc[skill] ?? 0) + 1;
    return acc;
  }, {});
  const topSkillGap =
    Object.entries(gapCounts).sort((a, b) => b[1] - a[1])[0]?.[0] ?? 'Vector DB';

  const avgAiScore =
    candidates.length === 0
      ? 0
      : Math.round(
          candidates.reduce((sum, c) => sum + c.aiScore, 0) / candidates.length
        );

  return {
    topSkillGap,
    avgAiScore,
    openToWorkCount: candidates.filter((c) => c.availability.toLowerCase().includes('immediate')).length,
    highIntegrityCount: candidates.filter((c) => c.integrityScore >= 90).length,
    hiddenGems: candidates
      .filter((c) => c.aiScore >= 85 && c.matchStatus !== 'excellent')
      .slice(0, 3),
    highRisk: candidates.filter((c) => c.risks.length > 0).slice(0, 3),
    excellentMatches: candidates.filter((c) => c.matchStatus === 'excellent').length,
  };
}
