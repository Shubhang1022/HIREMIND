'use client';

import { useMemo } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { toast } from 'sonner';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion';
import { ScoreBadge } from '@/components/ui/ScoreBadge';
import { MatchStatusBadge } from '@/components/ui/MatchStatusBadge';
import { Timeline } from '@/components/ui/Timeline';
import { LoadingState } from '@/components/ui/LoadingState';
import { ErrorState } from '@/components/ui/ErrorState';
import { useDemoCandidates } from '@/lib/useDemoData';
import { exportToJson } from '@/lib/export';
import {
  ArrowLeft,
  Calendar,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  MinusCircle,
  Sparkles,
  Download,
} from 'lucide-react';

export default function CandidateDetailsPage() {
  const params = useParams();
  const { candidates, loading, error, retry } = useDemoCandidates();

  const candidate = useMemo(
    () => candidates.find((c) => c.id === params.id),
    [candidates, params.id]
  );

  const handleExportProfile = () => {
    if (!candidate) return;
    try {
      exportToJson(candidate, `candidate-${candidate.id}-${Date.now()}.json`);
      toast.success('Candidate profile exported');
    } catch {
      toast.error('Export failed');
    }
  };

  if (loading) {
    return (
      <div className="p-4 lg:p-8 pt-20 lg:pt-24">
        <LoadingState rows={8} label="Loading candidate profile" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 lg:p-8 pt-20 lg:pt-24">
        <ErrorState message={error} onRetry={retry} />
      </div>
    );
  }

  if (!candidate) {
    return (
      <div className="p-8 pt-24 flex flex-col items-center justify-center min-h-[60vh]">
        <h2 className="text-xl font-semibold">Candidate not found</h2>
        <Link href="/ranking" className="mt-4 text-primary hover:underline">
          Back to rankings
        </Link>
      </div>
    );
  }

  return (
    <div className="p-4 lg:p-8 pt-20 lg:pt-24 space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center gap-4">
        <Link href="/ranking">
          <Button variant="outline" size="icon">
            <ArrowLeft className="w-4 h-4" />
          </Button>
        </Link>
        <div className="flex-1">
          <h1 className="text-2xl lg:text-3xl font-bold">{candidate.name}</h1>
          <p className="text-muted-foreground">Rank #{candidate.rank} · {candidate.role}</p>
        </div>
        <Button variant="outline" onClick={handleExportProfile}>
          <Download className="w-4 h-4 mr-2" />
          Export Profile
        </Button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Sparkles className="w-5 h-5 text-yellow-500" />
                AI Recruiter Summary
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-muted-foreground leading-relaxed">{candidate.summary}</p>
            </CardContent>
          </Card>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  <CheckCircle2 className="w-5 h-5 text-emerald-500" />
                  Why Selected
                </CardTitle>
              </CardHeader>
              <CardContent>
                {candidate.whySelected.length === 0 ? (
                  <p className="text-sm text-muted-foreground">Not recommended for shortlist.</p>
                ) : (
                  <ul className="space-y-2">
                    {candidate.whySelected.map((item, idx) => (
                      <li key={idx} className="text-sm">{item}</li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  <XCircle className="w-5 h-5 text-muted-foreground" />
                  Why Not Selected
                </CardTitle>
              </CardHeader>
              <CardContent>
                {candidate.whyNotSelected.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No major blockers identified.</p>
                ) : (
                  <ul className="space-y-2">
                    {candidate.whyNotSelected.map((item, idx) => (
                      <li key={idx} className="text-sm text-muted-foreground">{item}</li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
          </div>

          <Tabs defaultValue="skills" className="w-full">
            <TabsList>
              <TabsTrigger value="skills">Skills</TabsTrigger>
              <TabsTrigger value="timeline">Career Timeline</TabsTrigger>
              <TabsTrigger value="interview">Interview Copilot</TabsTrigger>
            </TabsList>
            <TabsContent value="skills" className="mt-4">
              <Card>
                <CardContent className="pt-6 space-y-4">
                  {candidate.skills.map((skill, idx) => (
                    <div key={idx}>
                      <div className="flex items-center justify-between mb-2">
                        <span className="font-medium">{skill.name}</span>
                        <span className="text-sm text-muted-foreground">{skill.category}</span>
                      </div>
                      <div className="h-2 bg-muted rounded-full overflow-hidden">
                        <div
                          className="h-full bg-gradient-to-r from-indigo-500 to-purple-600 rounded-full"
                          style={{ width: `${skill.proficiency}%` }}
                        />
                      </div>
                    </div>
                  ))}
                </CardContent>
              </Card>
            </TabsContent>
            <TabsContent value="timeline" className="mt-4">
              <Card>
                <CardContent className="pt-6">
                  <Timeline items={candidate.timeline} />
                </CardContent>
              </Card>
            </TabsContent>
            <TabsContent value="interview" className="mt-4">
              <Card>
                <CardContent className="pt-6">
                  <Accordion className="w-full">
                    {candidate.interviewQuestions.map((section, idx) => (
                      <AccordionItem key={idx} value={`item-${idx}`}>
                        <AccordionTrigger className="font-semibold">{section.category}</AccordionTrigger>
                        <AccordionContent>
                          <ul className="space-y-2">
                            {section.questions.map((q, qIdx) => (
                              <li key={qIdx} className="text-muted-foreground pl-4 border-l-2 border-primary/20">
                                {q}
                              </li>
                            ))}
                          </ul>
                        </AccordionContent>
                      </AccordionItem>
                    ))}
                  </Accordion>
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader><CardTitle>Match Scores</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <div className="flex justify-between items-center"><span>AI Score</span><ScoreBadge score={candidate.aiScore} /></div>
              <div className="flex justify-between items-center"><span>Match %</span><ScoreBadge score={candidate.matchPercent} /></div>
              <div className="flex justify-between items-center"><span>Integrity</span><ScoreBadge score={candidate.integrityScore} /></div>
              <div className="flex justify-between items-center"><span>Readiness</span><ScoreBadge score={candidate.hiringReadiness} /></div>
              <MatchStatusBadge status={candidate.matchStatus} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <AlertTriangle className="w-5 h-5 text-amber-500" />
                Risks
              </CardTitle>
            </CardHeader>
            <CardContent>
              {candidate.risks.length === 0 ? (
                <p className="text-sm text-muted-foreground">No significant risks flagged.</p>
              ) : (
                <ul className="space-y-2">
                  {candidate.risks.map((risk, idx) => (
                    <li key={idx} className="text-sm flex gap-2">
                      <AlertTriangle className="w-4 h-4 text-amber-500 shrink-0 mt-0.5" />
                      {risk}
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <MinusCircle className="w-5 h-5 text-muted-foreground" />
                Missing Skills
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="space-y-2">
                {candidate.missingSkills.map((skill, idx) => (
                  <li key={idx} className="text-sm text-muted-foreground">{skill}</li>
                ))}
              </ul>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="pt-6 text-sm text-muted-foreground space-y-2">
              <div className="flex items-center gap-2"><Calendar className="w-4 h-4" />{candidate.experience} years experience</div>
              <div>{candidate.location}</div>
              <div>Available: {candidate.availability}</div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
