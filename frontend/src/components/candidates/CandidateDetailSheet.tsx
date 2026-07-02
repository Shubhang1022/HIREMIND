'use client';

import { useEffect, useState } from 'react';
import {
  MapPin, Briefcase, GraduationCap, Award, Globe,
  Clock, CheckCircle, Star, X, Loader2,
  Calendar, Building2, Code2, MessageSquare,
} from 'lucide-react';
import { Sheet, SheetContent } from '@/components/ui/sheet';
import { Badge } from '@/components/ui/badge';
import { platformApi } from '@/lib/platform-api';

interface Props {
  projectId: string;
  candidateId: string | null;
  candidateName?: string;
  rankInfo?: {
    rank: number;
    aiScore: number;
    matchPercent: number;
    reasoning: string;
    hiringReadiness: string;
    strengths: string[];
    weaknesses: string[];
  };
  onClose: () => void;
}

const PROFICIENCY_COLORS: Record<string, string> = {
  expert: 'bg-purple-500/20 text-purple-300 border-purple-500/30',
  advanced: 'bg-indigo-500/20 text-indigo-300 border-indigo-500/30',
  intermediate: 'bg-blue-500/20 text-blue-300 border-blue-500/30',
  beginner: 'bg-muted/40 text-muted-foreground border-border/50',
};

function Section({ icon: Icon, title, children }: { icon: any; title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 pb-1 border-b border-border/40">
        <Icon className="w-4 h-4 text-indigo-400" />
        <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">{title}</h3>
      </div>
      {children}
    </div>
  );
}

export function CandidateDetailSheet({ projectId, candidateId, candidateName, rankInfo, onClose }: Props) {
  const [candidate, setCandidate] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!candidateId) { setCandidate(null); return; }
    setLoading(true);
    platformApi.candidates.get(projectId, candidateId)
      .then(setCandidate)
      .catch(() => setCandidate(null))
      .finally(() => setLoading(false));
  }, [projectId, candidateId]);

  const profile = candidate?.profile ?? {};
  const signals = candidate?.redrob_signals ?? {};
  const career = candidate?.career_history ?? [];
  const education = candidate?.education ?? [];
  const skills = candidate?.skills ?? [];
  const certs = candidate?.certifications ?? [];
  const langs = candidate?.languages ?? [];

  const displayName = profile.anonymized_name || candidateName || candidateId || 'Candidate';
  const initials = displayName.split(' ').map((w: string) => w[0]).join('').slice(0, 2).toUpperCase();

  return (
    <Sheet open={!!candidateId} onOpenChange={open => { if (!open) onClose(); }}>
      <SheetContent side="right" className="w-full sm:max-w-2xl overflow-y-auto p-0">
        {loading ? (
          <div className="flex items-center justify-center h-full">
            <Loader2 className="w-8 h-8 animate-spin text-indigo-400" />
          </div>
        ) : !candidate ? (
          <div className="flex items-center justify-center h-full text-muted-foreground">
            <p>Candidate data not found.</p>
          </div>
        ) : (
          <div className="flex flex-col h-full">
            {/* Header */}
            <div className="bg-gradient-to-br from-indigo-500/10 to-purple-500/10 border-b border-border/50 p-6">
              <div className="flex items-start gap-4">
                <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white text-xl font-bold shrink-0 shadow-lg shadow-indigo-500/20">
                  {initials}
                </div>
                <div className="flex-1 min-w-0">
                  <h2 className="text-xl font-bold truncate">{displayName}</h2>
                  <p className="text-indigo-300 font-medium mt-0.5 truncate">{profile.current_title}</p>
                  {profile.current_company && (
                    <p className="text-sm text-muted-foreground flex items-center gap-1.5 mt-1">
                      <Building2 className="w-3.5 h-3.5" /> {profile.current_company}
                    </p>
                  )}
                  {profile.location && (
                    <p className="text-sm text-muted-foreground flex items-center gap-1.5 mt-0.5">
                      <MapPin className="w-3.5 h-3.5" /> {profile.location}
                    </p>
                  )}
                  <div className="flex flex-wrap gap-2 mt-3">
                    {profile.years_of_experience != null && (
                      <Badge variant="secondary" className="text-xs">
                        {profile.years_of_experience}y experience
                      </Badge>
                    )}
                    {signals.open_to_work_flag && (
                      <Badge className="text-xs bg-green-500/20 text-green-400 border-green-500/30">
                        <CheckCircle className="w-3 h-3 mr-1" /> Open to Work
                      </Badge>
                    )}
                    {signals.notice_period_days != null && (
                      <Badge variant="secondary" className="text-xs">
                        <Clock className="w-3 h-3 mr-1" /> {signals.notice_period_days}d notice
                      </Badge>
                    )}
                  </div>
                </div>
              </div>

              {/* Rank info if from ranking */}
              {rankInfo && (
                <>
                  <div className="mt-4 grid grid-cols-3 gap-3">
                    {[
                      { label: 'Rank', value: typeof rankInfo.rank === 'number' ? `#${rankInfo.rank}` : rankInfo.rank, color: 'text-amber-400' },
                      { label: 'Match', value: `${rankInfo.matchPercent}%`, color: 'text-green-400' },
                      { label: 'Readiness', value: rankInfo.hiringReadiness, color: 'text-indigo-300' },
                    ].map(m => (
                      <div key={m.label} className="text-center p-2.5 rounded-xl bg-background/50 border border-border/40">
                        <p className={`text-lg font-bold capitalize ${m.color}`}>{m.value}</p>
                        <p className="text-xs text-muted-foreground mt-0.5">{m.label}</p>
                      </div>
                    ))}
                  </div>

                  {/* Score Breakdown (Match Explanation - PART 5) */}
                  {(rankInfo as any).roleMatchPercent !== undefined && (
                    <div className="mt-4 p-3 rounded-xl bg-background/50 border border-border/40 space-y-2">
                      <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider text-center">Match Explanation Breakdown</h4>
                      <div className="grid grid-cols-5 gap-2 text-center">
                        {[
                          { label: 'Overall', value: `${rankInfo.matchPercent}%`, color: 'text-green-400 font-bold' },
                          { label: 'Role Match', value: `${(rankInfo as any).roleMatchPercent}%`, color: 'text-indigo-300' },
                          { label: 'Critical Skill', value: `${(rankInfo as any).criticalSkillMatchPercent}%`, color: 'text-purple-300' },
                          { label: 'Experience', value: `${(rankInfo as any).experienceMatchPercent}%`, color: 'text-blue-300' },
                          { label: 'Semantic', value: `${(rankInfo as any).semanticSimilarityPercent}%`, color: 'text-emerald-300' },
                        ].map(b => (
                          <div key={b.label} className="p-1.5 rounded-lg bg-muted/20 border border-border/20">
                            <p className={`text-sm font-semibold ${b.color}`}>{b.value}</p>
                            <p className="text-[10px] text-muted-foreground mt-0.5 line-clamp-1 leading-none">{b.label}</p>
                          </div>
                        ))}
                      </div>
                      {(rankInfo as any).criticalSkillCoverage && (
                        <p className="text-xs text-muted-foreground text-center mt-2 border-t border-border/20 pt-1.5">
                          Critical Skill Coverage: <span className="font-semibold text-indigo-300">{(rankInfo as any).criticalSkillCoverage} ({(rankInfo as any).criticalSkillCoveragePercent}%)</span>
                        </p>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>

            {/* Body */}
            <div className="flex-1 overflow-y-auto p-6 space-y-6">

              {/* AI Reasoning */}
              {rankInfo?.reasoning && (
                <Section icon={MessageSquare} title="AI Analysis">
                  <p className="text-sm text-muted-foreground italic bg-muted/20 rounded-xl p-4 border border-border/40">
                    "{rankInfo.reasoning}"
                  </p>
                  {rankInfo.strengths?.length > 0 && (
                    <div className="flex flex-wrap gap-2">
                      {rankInfo.strengths.map(s => (
                        <span key={s} className="text-xs px-2.5 py-1 rounded-full bg-green-500/10 text-green-400 border border-green-500/20">{s}</span>
                      ))}
                    </div>
                  )}
                  {rankInfo.weaknesses?.length > 0 && (
                    <div className="flex flex-wrap gap-2">
                      {rankInfo.weaknesses.map(w => (
                        <span key={w} className="text-xs px-2.5 py-1 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20">{w}</span>
                      ))}
                    </div>
                  )}
                </Section>
              )}

              {/* Summary */}
              {profile.summary && (
                <Section icon={MessageSquare} title="Summary">
                  <p className="text-sm text-muted-foreground leading-relaxed">{profile.summary}</p>
                </Section>
              )}

              {/* Skills */}
              {skills.length > 0 && (
                <Section icon={Code2} title={`Skills (${skills.length})`}>
                  <div className="flex flex-wrap gap-2">
                    {skills.map((s: any, i: number) => (
                      <span key={i} className={`text-xs px-2.5 py-1 rounded-full border font-medium ${PROFICIENCY_COLORS[s.proficiency] || PROFICIENCY_COLORS.beginner}`}>
                        {s.name}
                        {s.proficiency === 'expert' && <Star className="w-3 h-3 inline ml-1 fill-current" />}
                        {s.duration_months && s.duration_months > 0 && (
                          <span className="ml-1 opacity-60">·{Math.round(s.duration_months / 12)}y</span>
                        )}
                      </span>
                    ))}
                  </div>
                </Section>
              )}

              {/* Career History */}
              {career.length > 0 && (
                <Section icon={Briefcase} title="Experience">
                  <div className="space-y-4">
                    {career.map((role: any, i: number) => (
                      <div key={i} className={`relative pl-4 ${i < career.length - 1 ? 'pb-4 border-l border-border/40' : ''}`}>
                        <div className="absolute -left-1.5 top-1.5 w-3 h-3 rounded-full bg-indigo-500/40 border-2 border-indigo-500/60" />
                        <div className="flex flex-wrap items-start justify-between gap-2">
                          <div>
                            <p className="font-semibold text-sm">{role.title}</p>
                            <p className="text-sm text-indigo-300/80">{role.company}</p>
                          </div>
                          <div className="text-right shrink-0">
                            <p className="text-xs text-muted-foreground">
                              {role.start_date?.slice(0, 7)} — {role.is_current ? 'Present' : role.end_date?.slice(0, 7)}
                            </p>
                            {role.duration_months != null && (
                              <p className="text-xs text-muted-foreground/60">
                                {role.duration_months >= 12
                                  ? `${Math.floor(role.duration_months / 12)}y ${role.duration_months % 12}m`
                                  : `${role.duration_months}m`}
                              </p>
                            )}
                          </div>
                        </div>
                        {role.description && (
                          <p className="mt-2 text-xs text-muted-foreground leading-relaxed line-clamp-4">{role.description}</p>
                        )}
                      </div>
                    ))}
                  </div>
                </Section>
              )}

              {/* Education */}
              {education.length > 0 && (
                <Section icon={GraduationCap} title="Education">
                  <div className="space-y-3">
                    {education.map((edu: any, i: number) => (
                      <div key={i} className="p-3 rounded-xl bg-muted/20 border border-border/40">
                        <p className="font-medium text-sm">{edu.degree} — {edu.field_of_study}</p>
                        <p className="text-sm text-indigo-300/80">{edu.institution}</p>
                        <p className="text-xs text-muted-foreground mt-1">
                          {edu.start_year} – {edu.end_year}
                          {edu.grade && <span className="ml-2 text-green-400/80">{edu.grade}</span>}
                          {edu.tier && <span className="ml-2 capitalize opacity-60">{edu.tier.replace('_', ' ')}</span>}
                        </p>
                      </div>
                    ))}
                  </div>
                </Section>
              )}

              {/* Certifications */}
              {certs.length > 0 && (
                <Section icon={Award} title="Certifications">
                  <div className="space-y-2">
                    {certs.map((c: any, i: number) => (
                      <div key={i} className="flex items-center justify-between p-3 rounded-xl bg-muted/20 border border-border/40">
                        <div>
                          <p className="text-sm font-medium">{c.name}</p>
                          <p className="text-xs text-muted-foreground">{c.issuer || c.issuing_organization}</p>
                        </div>
                        {(c.year || c.issue_date) && (
                          <p className="text-xs text-muted-foreground shrink-0 ml-2">{c.year || c.issue_date?.slice(0, 4)}</p>
                        )}
                      </div>
                    ))}
                  </div>
                </Section>
              )}

              {/* Languages */}
              {langs.length > 0 && (
                <Section icon={Globe} title="Languages">
                  <div className="flex flex-wrap gap-2">
                    {langs.map((l: any, i: number) => (
                      <Badge key={i} variant="secondary" className="text-xs">
                        {l.language || l.name} · {l.proficiency}
                      </Badge>
                    ))}
                  </div>
                </Section>
              )}

              {/* Platform Signals */}
              {Object.keys(signals).length > 0 && (
                <Section icon={Calendar} title="Platform Signals">
                  <div className="grid grid-cols-2 gap-2">
                    {[
                      { label: 'Response Rate', value: signals.recruiter_response_rate != null ? `${Math.round(signals.recruiter_response_rate * 100)}%` : '—' },
                      { label: 'Avg Response', value: signals.avg_response_time_hours != null ? `${signals.avg_response_time_hours}h` : '—' },
                      { label: 'Profile Score', value: signals.profile_completeness_score != null ? `${signals.profile_completeness_score}%` : '—' },
                      { label: 'GitHub Score', value: signals.github_activity_score === -1 ? 'Not linked' : signals.github_activity_score != null ? String(signals.github_activity_score) : '—' },
                      { label: 'Saved by Recruiters', value: signals.saved_by_recruiters_30d != null ? String(signals.saved_by_recruiters_30d) : '—' },
                      { label: 'Preferred Mode', value: signals.preferred_work_mode || '—' },
                    ].map(s => (
                      <div key={s.label} className="p-2.5 rounded-lg bg-muted/20 border border-border/40">
                        <p className="text-xs text-muted-foreground">{s.label}</p>
                        <p className="text-sm font-medium mt-0.5 capitalize">{s.value}</p>
                      </div>
                    ))}
                  </div>
                  {signals.expected_salary_range_inr_lpa && (
                    <div className="p-3 rounded-xl bg-muted/20 border border-border/40">
                      <p className="text-xs text-muted-foreground">Expected Salary</p>
                      <p className="text-sm font-medium mt-0.5">
                        ₹{signals.expected_salary_range_inr_lpa.min}–{signals.expected_salary_range_inr_lpa.max} LPA
                      </p>
                    </div>
                  )}
                </Section>
              )}
            </div>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
